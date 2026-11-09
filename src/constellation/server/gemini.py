"""The Gemini protocol listener (spec 0.24.1): one request line, one response, close."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import ssl
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from constellation.server.tls import SniTable

WRITE_TIMEOUT = 30.0
log = logging.getLogger("constellation.server.gemini")
MAX_REQUEST = 1024
EventSink = Callable[[dict[str, object]], Awaitable[None] | None]


@dataclass(slots=True)
class GeminiRoute:
    """What the router gives the listener for one hostname."""

    site: str
    root: Path
    lang: str = "en"


Router = Callable[[str], GeminiRoute | None]


class GeminiServer:
    def __init__(
        self,
        router: Router,
        sni: SniTable,
        *,
        handshake_timeout: float = 10,
        read_timeout: float = 10,
        max_connections: int = 512,
        on_event: EventSink | None = None,
    ) -> None:
        self.router = router
        self.sni = sni
        self.handshake_timeout = handshake_timeout
        self.read_timeout = read_timeout
        self.semaphore = asyncio.Semaphore(max_connections)
        self.on_event = on_event
        self.servers: list[asyncio.base_events.Server] = []

    async def start(self, binds: list[str], port: int) -> None:
        for bind in binds:
            server = await asyncio.start_server(
                self._handle,
                bind,
                port,
                ssl=self.sni.default,
                ssl_handshake_timeout=self.handshake_timeout,
                limit=MAX_REQUEST + 2,
            )
            self.servers.append(server)
            log.info("gemini listening on %s:%d", bind, port)

    async def stop(self) -> None:
        for s in self.servers:
            s.close()
        for s in self.servers:
            await s.wait_closed()
        self.servers.clear()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        started = time.monotonic()
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else ""
        ssl_obj = writer.get_extra_info("ssl_object")
        tls_version = ssl_obj.version() if ssl_obj else None
        status, meta, nbytes, url_text, site = 59, "Bad request", 0, "", ""
        async with self.semaphore:
            try:
                try:
                    line = await asyncio.wait_for(
                        reader.readuntil(b"\r\n"), timeout=self.read_timeout
                    )
                except (
                    asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError,
                    TimeoutError,
                    ValueError,
                ):
                    await self._respond(writer, 59, "Bad request")
                    return
                raw = line[:-2]
                if len(raw) > MAX_REQUEST:
                    await self._respond(writer, 59, "Request too long")
                    return
                try:
                    url_text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    await self._respond(writer, 59, "Bad request")
                    return
                status, meta, body, site = self._resolve(url_text)
                nbytes = await self._respond(writer, status, meta, body)
            except (ConnectionError, ssl.SSLError, OSError) as e:
                log.debug("connection error: %s", e)
            finally:
                writer.close()
                with contextlib.suppress(TimeoutError, OSError, ssl.SSLError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=5)
                if self.on_event is not None:
                    try:
                        result = self.on_event(
                            {
                                "platform": "gemini",
                                "site": site,
                                "url": url_text,
                                "status": status,
                                "meta": meta,
                                "bytes": nbytes,
                                "ip": ip,
                                "tls_version": tls_version,
                                "duration_ms": int((time.monotonic() - started) * 1000),
                            }
                        )
                        if result is not None:
                            await result
                    except Exception:  # noqa: BLE001
                        log.exception("analytics sink failed")

    def _resolve(self, url_text: str) -> tuple[int, str, bytes | Path | None, str]:
        try:
            parts = urllib.parse.urlsplit(url_text)
        except ValueError:
            return 59, "Bad request", None, ""
        if parts.scheme.lower() != "gemini":
            return 53, "Unsupported scheme", None, ""
        if parts.username or parts.password or parts.fragment:
            return 59, "Bad request", None, ""
        host = (parts.hostname or "").lower()
        if not host:
            return 59, "Bad request", None, ""
        try:
            port = parts.port
        except ValueError:
            return 59, "Bad request", None, ""
        route = self.router(host)
        if route is None:
            return 53, "Proxy request refused", None, ""
        path = urllib.parse.unquote(parts.path or "/")
        if "\x00" in path or any(seg in ("..", "") and seg == ".." for seg in path.split("/")):
            return 51, "Not found", None, route.site
        segments = [s for s in path.split("/") if s]
        if any(s.startswith(".") for s in segments):
            return 51, "Not found", None, route.site
        target = route.root.joinpath(*segments) if segments else route.root
        try:
            resolved = target.resolve()
            resolved.relative_to(route.root.resolve())
        except (ValueError, OSError):
            return 51, "Not found", None, route.site
        if resolved.is_dir():
            if not path.endswith("/"):
                return (
                    31,
                    f"gemini://{host}{'' if port in (None, 1965) else ':' + str(port)}{parts.path}/",
                    None,
                    route.site,
                )
            resolved = resolved / "index.gmi"
        if not resolved.is_file():
            return 51, "Not found", None, route.site
        mime = _media_type(resolved)
        if mime == "text/gemini":
            mime = f"text/gemini; lang={route.lang}" if route.lang else "text/gemini"
        return 20, mime, resolved, route.site

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, meta: str, body: bytes | Path | None = None
    ) -> int:
        header = f"{status} {meta}\r\n".encode()
        writer.write(header)
        sent = 0
        if status == 20 and body is not None:
            if isinstance(body, Path):
                with body.open("rb") as fh:
                    while chunk := fh.read(65536):
                        writer.write(chunk)
                        sent += len(chunk)
                        # A client that stops reading is dropped instead of holding a slot.
                        await asyncio.wait_for(writer.drain(), timeout=WRITE_TIMEOUT)
            else:
                writer.write(body)
                sent = len(body)
        await asyncio.wait_for(writer.drain(), timeout=WRITE_TIMEOUT)
        return sent


def _media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".gmi":
        return "text/gemini"
    if ext in (".txt", ".md"):
        return "text/plain; charset=utf-8"
    if ext == ".xml" and path.name in ("feed.xml", "atom.xml"):
        return "application/atom+xml"
    guess, _ = mimetypes.guess_type(path.name)
    return guess or "application/octet-stream"
