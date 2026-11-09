"""The Gopher listener (RFC 1436): one selector line, a menu or file, done.

Gopher has no hostname in the request, so one listener serves exactly one site. Menus are stored
as gophermaps already in wire format (type, display, selector, host, port per line); the server
appends the ``.`` terminator. Text files are dot-stuffed; everything else is sent raw.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger("constellation.server.gopher")
EventSink = Callable[[dict[str, object]], Awaitable[None] | None]
TEXT_EXT = (".txt", ".md", ".gmi", ".xml", ".json", ".csv")


class GopherServer:
    def __init__(
        self,
        root_for: Callable[[], tuple[str, Path] | None],
        *,
        host: str,
        port: int = 70,
        web_url: str = "",
        read_timeout: float = 10,
        max_connections: int = 256,
        on_event: EventSink | None = None,
    ) -> None:
        self.root_for = root_for
        self.host = host
        self.port = port
        self.web_url = web_url
        self.read_timeout = read_timeout
        self.semaphore = asyncio.Semaphore(max_connections)
        self.on_event = on_event
        self.servers: list[asyncio.base_events.Server] = []

    async def start(self, binds: list[str], port: int | None = None) -> None:
        for bind in binds:
            server = await asyncio.start_server(self._handle, bind, port or self.port, limit=1024)
            self.servers.append(server)
            log.info("gopher listening on %s:%d", bind, port or self.port)

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
        selector, status, nbytes, site = "", 51, 0, ""
        async with self.semaphore:
            try:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=self.read_timeout)
                except (TimeoutError, asyncio.LimitOverrunError, ValueError):
                    writer.close()
                    return
                raw = line.rstrip(b"\r\n")
                if raw.startswith(b"GET ") or raw.startswith(b"HEAD "):
                    body = self._http_hint()
                    writer.write(body)
                    status, nbytes, selector = 20, len(body), "(http)"
                else:
                    selector = raw.split(b"\t", 1)[0].decode("utf-8", "replace")
                    status, nbytes, site = await self._serve(writer, selector)
                await writer.drain()
            except (ConnectionError, OSError) as e:
                log.debug("connection error: %s", e)
            finally:
                writer.close()
                with contextlib.suppress(TimeoutError, OSError):
                    await asyncio.wait_for(writer.wait_closed(), timeout=5)
                if self.on_event is not None:
                    try:
                        result = self.on_event(
                            {
                                "platform": "gopher",
                                "site": site,
                                "url": selector or "/",
                                "status": status,
                                "bytes": nbytes,
                                "ip": ip,
                                "duration_ms": int((time.monotonic() - started) * 1000),
                            }
                        )
                        if result is not None:
                            await result
                    except Exception:  # noqa: BLE001
                        log.exception("analytics sink failed")

    def _http_hint(self) -> bytes:
        target = self.web_url or "/"
        return (
            f"HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
            f"<!doctype html><title>Gopher hole</title><p>This is a Gopher server. Use a Gopher client, or read the web version at "
            f'<a href="{target}">{target}</a>.</p>'
        ).encode()

    async def _serve(self, writer: asyncio.StreamWriter, selector: str) -> tuple[int, int, str]:
        route = self.root_for()
        if route is None:
            return self._error(writer, "No site is served here"), 0, ""
        site, root = route
        if selector.startswith("URL:"):
            body = self._url_page(selector[4:])
            writer.write(body)
            return 20, len(body), site
        if "\x00" in selector or ".." in selector.split("/"):
            return self._error(writer, "Not found"), 0, site
        segments = [s for s in selector.split("/") if s]
        if any(s.startswith(".") for s in segments):
            return self._error(writer, "Not found"), 0, site
        target = root.joinpath(*segments) if segments else root
        try:
            resolved = target.resolve()
            resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            return self._error(writer, "Not found"), 0, site
        if resolved.is_dir():
            gmap = resolved / "gophermap"
            if gmap.is_file():
                data = gmap.read_bytes()
                if not data.endswith(b"\r\n"):
                    data += b"\r\n"
                data += b".\r\n"
                writer.write(data)
                return 20, len(data), site
            return self._error(writer, "Not found"), 0, site
        if not resolved.is_file():
            return self._error(writer, "Not found"), 0, site
        data = resolved.read_bytes()
        if resolved.suffix.lower() in TEXT_EXT:
            lines = data.decode("utf-8", "replace").split("\n")
            out = "\r\n".join(("." + ln if ln.startswith(".") else ln).rstrip("\r") for ln in lines)
            if not out.endswith("\r\n"):
                out += "\r\n"
            data = out.encode() + b".\r\n"
        writer.write(data)
        return 20, len(data), site

    def _error(self, writer: asyncio.StreamWriter, message: str) -> int:
        writer.write(f"3{message}\tfake\t{self.host}\t{self.port}\r\n.\r\n".encode())
        return 51

    def _url_page(self, url: str) -> bytes:
        from html import escape

        u = escape(url, quote=True)
        return (
            f'<!doctype html><html><head><meta http-equiv="refresh" content="2;url={u}"><title>Redirect</title></head>'
            f'<body><p>You are following a link to <a href="{u}">{u}</a>.</p></body></html>'
        ).encode()
