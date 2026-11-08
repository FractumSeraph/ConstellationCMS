"""Run the server: HTTP (uvicorn) and Gemini listeners sharing one release store."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn

from constellation.server.config import ServerConfig
from constellation.server.gemini import GeminiRoute, GeminiServer
from constellation.server.http import HttpApp
from constellation.server.releases import Store
from constellation.server.tls import SniTable, ensure_self_signed, server_context

log = logging.getLogger("constellation.server")
EventSink = Callable[[dict[str, object]], Awaitable[None] | None]


class Server:
    def __init__(self, cfg: ServerConfig, *, on_event: EventSink | None = None) -> None:
        self.cfg = cfg
        self.store = Store(cfg.data)
        self.http = HttpApp(cfg, self.store, on_event=on_event, on_release=self.on_release)
        self.gemini_routes: dict[str, GeminiRoute] = {}
        self.sni: SniTable | None = None
        self.gemini: GeminiServer | None = None
        self.on_event = on_event
        self.reload_gemini()

    # ---- routing ---------------------------------------------------------------------------
    def reload_gemini(self) -> None:
        routes: dict[str, GeminiRoute] = {}
        contexts: dict[str, ssl.SSLContext] = {}
        table = self.store.hostname_table().get("gemini", {})
        for host, sid in table.items():
            root = self.store.site(sid).current_dir("gemini")
            if root is None:
                continue
            lang = str(self.store.site(sid).site_config().get("language", "en"))
            routes[host] = GeminiRoute(sid, root, lang)
            if self.cfg.gemini.enabled:
                key, cert = ensure_self_signed(
                    self.cfg.data, "gemini", host, years=self.cfg.gemini.cert_years
                )
                contexts[host] = server_context(cert, key)
        self.gemini_routes = routes
        if self.sni is not None:
            self.sni.set(contexts)

    def on_release(self, site: str) -> None:
        log.info("release switched for %s; reloading routes", site)
        self.reload_gemini()

    def route_gemini(self, host: str) -> GeminiRoute | None:
        return self.gemini_routes.get(host.lower())

    # ---- running ---------------------------------------------------------------------------
    async def serve(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        stop = asyncio.Event()

        if self.cfg.gemini.enabled:
            key, cert = ensure_self_signed(
                self.cfg.data,
                "gemini",
                self.cfg.admin.hostname or "localhost",
                years=self.cfg.gemini.cert_years,
            )
            self.sni = SniTable(server_context(cert, key))
            self.reload_gemini()
            self.gemini = GeminiServer(
                self.route_gemini,
                self.sni,
                handshake_timeout=self.cfg.limits.gemini_handshake_timeout_s,
                read_timeout=self.cfg.limits.request_timeout_s,
                max_connections=self.cfg.limits.max_connections,
                on_event=self.on_event,
            )
            await self.gemini.start(self.cfg.gemini.bind, self.cfg.gemini.port)

        if self.cfg.https.tls_mode == "behind-proxy":
            http_cfg = uvicorn.Config(
                self.http.app,
                host=self.cfg.https.plain_bind,
                port=self.cfg.https.plain_port,
                log_level="warning",
                proxy_headers=False,
                server_header=False,
            )
            tasks.append(asyncio.create_task(uvicorn.Server(http_cfg).serve()))
        else:
            if self.cfg.http.enabled:
                plain = uvicorn.Config(
                    self.http.app,
                    host=self.cfg.http.bind[0],
                    port=self.cfg.http.port,
                    log_level="warning",
                    server_header=False,
                )
                tasks.append(asyncio.create_task(uvicorn.Server(plain).serve()))
            if self.cfg.https.enabled:
                key, cert = self._https_cert()
                tls = uvicorn.Config(
                    self.http.app,
                    host=self.cfg.http.bind[0],
                    port=self.cfg.https.port,
                    log_level="warning",
                    ssl_keyfile=str(key),
                    ssl_certfile=str(cert),
                    server_header=False,
                )
                tasks.append(asyncio.create_task(uvicorn.Server(tls).serve()))

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        log.info("server up (tls mode %s)", self.cfg.https.tls_mode)
        await stop.wait()
        if self.gemini:
            await self.gemini.stop()
        for t in tasks:
            t.cancel()

    def _https_cert(self) -> tuple[Path, Path]:
        """External mode reads files; ACME arrives in milestone 6; until then a self-signed fallback."""
        if self.cfg.https.tls_mode == "external" and self.cfg.https.cert_dir:
            d = Path(self.cfg.https.cert_dir)
            key, cert = d / "privkey.pem", d / "fullchain.pem"
            if key.is_file() and cert.is_file():
                return key, cert
        return ensure_self_signed(self.cfg.data, "web", self.cfg.admin.hostname or "localhost")


def run(config_path: Path) -> None:
    from constellation.server.config import load_server_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_server_config(config_path)
    asyncio.run(Server(cfg).serve())
