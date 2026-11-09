"""Run the server: HTTP (uvicorn) and Gemini listeners sharing one release store."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import ssl
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn

from constellation.server.acme import AcmeClient, ChallengeStore, Orchestrator
from constellation.server.analytics import Analytics, UmamiForwarder
from constellation.server.config import ServerConfig
from constellation.server.gemini import GeminiRoute, GeminiServer
from constellation.server.gopher import GopherServer
from constellation.server.http import HttpApp
from constellation.server.releases import Store
from constellation.server.tls import (
    SniTable,
    ensure_self_signed,
    external_contexts,
    https_contexts,
    server_context,
)

log = logging.getLogger("constellation.server")
EventSink = Callable[[dict[str, object]], Awaitable[None] | None]


class Server:
    def __init__(self, cfg: ServerConfig, *, on_event: EventSink | None = None) -> None:
        self.cfg = cfg
        self.store = Store(cfg.data)
        self.analytics: Analytics | None = None
        self.forwarder: UmamiForwarder | None = None
        if cfg.analytics.enabled:
            self.forwarder = UmamiForwarder()
            self.analytics = Analytics(
                cfg.data,
                ip_policy=cfg.analytics.ip_policy,
                retention_days=cfg.analytics.retention_days,
                forwarder=self.forwarder,
                site_configs=lambda sid: self.store.site(sid).site_config(),
            )
        self._external_sink = on_event
        self.challenges = ChallengeStore()
        self.acme: Orchestrator | None = None
        if cfg.https.tls_mode == "acme":
            issuer = AcmeClient(
                cfg.https.acme_directory, cfg.https.acme_email, cfg.data / "certs" / "acme-account"
            )
            self.acme = Orchestrator(
                cfg.data, issuer, self.challenges, self.web_hostnames, on_change=self.reload_https
            )
        self.https_sni: SniTable | None = None
        self.http = HttpApp(cfg, self.store, on_event=self.on_event, on_release=self.on_release)
        self.http.analytics = self.analytics
        self.http.challenges = self.challenges
        self.http.server = self
        self.gemini_routes: dict[str, GeminiRoute] = {}
        self.sni: SniTable | None = None
        self.gemini: GeminiServer | None = None
        self.gopher: GopherServer | None = None
        self.reload_gemini()

    def on_event(self, event: dict[str, object]) -> None:
        if self.analytics is not None:
            try:
                self.analytics.record(event)
            except Exception:  # noqa: BLE001
                log.exception("analytics record failed")
        if self._external_sink is not None:
            result = self._external_sink(event)
            if result is not None:
                import asyncio as _asyncio

                _asyncio.ensure_future(result)

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

    def web_hostnames(self) -> list[str]:
        hosts = set(self.store.hostname_table().get("web", {}))
        if self.cfg.admin.hostname:
            hosts.add(self.cfg.admin.hostname.lower())
        if self.cfg.studio.enabled and self.cfg.studio.hostname:
            hosts.add(self.cfg.studio.hostname.lower())
        return sorted(hosts)

    def reload_https(self) -> None:
        if self.https_sni is None:
            return
        # The listening socket keeps its default context; new names arrive through SNI.
        _default, contexts = self._https_contexts()
        self.https_sni.set(contexts)

    def on_release(self, site: str) -> None:
        log.info("release switched for %s; reloading routes", site)
        self.reload_gemini()
        self.reload_https()
        if self.acme is not None:
            threading.Thread(
                target=self.acme.ensure_all, daemon=True, name="acme-on-release"
            ).start()

    async def renewal_loop(self) -> None:
        while True:
            if self.acme is not None:
                await asyncio.to_thread(self.acme.ensure_all)
            await asyncio.sleep(12 * 3600)

    def gopher_root(self) -> tuple[str, Path] | None:
        sites = [self.cfg.gopher.site] if self.cfg.gopher.site else self.store.sites()
        for sid in sites:
            root = self.store.site(sid).current_dir("gopher")
            if root is not None:
                return sid, root
        return None

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

        if self.cfg.gopher.enabled:
            gopher_host = (
                self.cfg.gopher.site
                and str(
                    self.store.site(self.cfg.gopher.site)
                    .site_config()
                    .get("hostnames", {})
                    .get("gopher", [""])[0]
                )
                or self.cfg.admin.hostname
                or "localhost"
            )
            web = ""
            route = self.gopher_root()
            if route is not None:
                web_hosts = (
                    self.store.site(route[0]).site_config().get("hostnames", {}).get("web", [])
                )
                web = f"https://{web_hosts[0]}/" if web_hosts else ""
            self.gopher = GopherServer(
                self.gopher_root,
                host=gopher_host,
                port=self.cfg.gopher.port,
                web_url=web,
                read_timeout=self.cfg.limits.request_timeout_s,
                on_event=self.on_event,
            )
            await self.gopher.start(
                self.cfg.gopher.bind or self.cfg.gemini.bind, self.cfg.gopher.port
            )

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
                for host in self.cfg.http.bind:
                    plain = uvicorn.Config(
                        self.http.app,
                        host=host,
                        port=self.cfg.http.port,
                        log_level="warning",
                        server_header=False,
                    )
                    tasks.append(asyncio.create_task(uvicorn.Server(plain).serve()))
            if self.cfg.https.enabled:
                key, cert = self._https_cert()
                default, contexts = self._https_contexts()
                self.https_sni = SniTable(default)
                self.https_sni.set(contexts)
                for host in self.cfg.http.bind:
                    tls = uvicorn.Config(
                        self.http.app,
                        host=host,
                        port=self.cfg.https.port,
                        log_level="warning",
                        ssl_keyfile=str(key),
                        ssl_certfile=str(cert),
                        server_header=False,
                    )
                    tls.load()
                    tls.ssl = default
                    tasks.append(asyncio.create_task(uvicorn.Server(tls).serve()))
                if self.acme is not None:
                    tasks.append(asyncio.create_task(self.renewal_loop()))

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        if self.forwarder is not None:
            self.forwarder.start()
        log.info("server up (tls mode %s)", self.cfg.https.tls_mode)
        await stop.wait()
        if self.forwarder is not None:
            self.forwarder.stop()
        if self.gemini:
            await self.gemini.stop()
        if self.gopher:
            await self.gopher.stop()
        for t in tasks:
            t.cancel()

    def _https_contexts(self) -> tuple[ssl.SSLContext, dict[str, ssl.SSLContext]]:
        if self.cfg.https.tls_mode == "external":
            if not self.cfg.https.cert_dir:
                raise RuntimeError('tls_mode = "external" needs https.cert_dir in server.toml')
            return external_contexts(Path(self.cfg.https.cert_dir), self.web_hostnames())
        return https_contexts(
            self.cfg.data,
            self.web_hostnames(),
            fallback_host=self.cfg.admin.hostname or "localhost",
        )

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
