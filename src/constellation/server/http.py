"""The HTTP side: static files from the active release per hostname, plus the publish and admin
API under ``/_constellation/`` on the admin hostname."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from constellation import __version__
from constellation.server.config import ServerConfig
from constellation.server.releases import ReleaseError, Store, verify_admin_token

log = logging.getLogger("constellation.server.http")
EventSink = Callable[[dict[str, object]], Awaitable[None] | None]
MAX_BLOB = 256 * 1024 * 1024
MAX_MANIFEST = 32 * 1024 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class HttpApp:
    def __init__(
        self,
        cfg: ServerConfig,
        store: Store,
        *,
        on_event: EventSink | None = None,
        on_release: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.on_event = on_event
        self.on_release = on_release
        self.table: dict[str, str] = {}
        self.canonical: dict[str, str] = {}
        self.redirects: dict[str, list[dict[str, Any]]] = {}
        self.headers: dict[str, dict[str, str]] = {}
        self.site_lang: dict[str, str] = {}
        self._rate: dict[str, list[float]] = {}
        self.reload()
        self.app = Starlette(
            routes=[
                Route("/_constellation/healthz", self.healthz, methods=["GET"]),
                Route("/_constellation/sites", self.sites, methods=["GET"]),
                Route("/_constellation/sites/{site}/manifest", self.manifest, methods=["GET"]),
                Route(
                    "/_constellation/sites/{site}/releases", self.releases, methods=["GET", "POST"]
                ),
                Route("/_constellation/sites/{site}/blobs/{sha}", self.put_blob, methods=["PUT"]),
                Route(
                    "/_constellation/sites/{site}/releases/{rid}/commit",
                    self.commit,
                    methods=["POST"],
                ),
                Route(
                    "/_constellation/sites/{site}/releases/{rid}/activate",
                    self.activate,
                    methods=["POST"],
                ),
                Route("/_constellation/sites/{site}/tokens", self.tokens, methods=["GET", "POST"]),
                Route(
                    "/_constellation/sites/{site}/tokens/{tid}",
                    self.revoke_token,
                    methods=["DELETE"],
                ),
                Route("/_constellation/sites/{site}", self.retire_site, methods=["DELETE"]),
                Route("/_constellation/status", self.status, methods=["GET"]),
                Route("/{path:path}", self.static, methods=["GET", "HEAD"]),
            ]
        )

    # ---- routing tables ------------------------------------------------------------------
    def reload(self) -> None:
        table = self.store.hostname_table().get("web", {})
        canonical: dict[str, str] = {}
        redirects: dict[str, list[dict[str, Any]]] = {}
        headers: dict[str, dict[str, str]] = {}
        for sid in self.store.sites():
            cfg = self.store.site(sid).site_config()
            hosts = [str(h).lower() for h in cfg.get("hostnames", {}).get("web", [])]
            canon = str(cfg.get("canonical", {}).get("web") or (hosts[0] if hosts else "")).lower()
            for h in hosts:
                canonical[h] = canon
            redirects[sid] = list(cfg.get("redirects", []))
            headers[sid] = {
                str(k): str(v) for k, v in cfg.get("headers", {}).get("web", {}).items()
            }
            self.site_lang[sid] = str(cfg.get("language", "en"))
        self.table, self.canonical, self.redirects, self.headers = (
            table,
            canonical,
            redirects,
            headers,
        )

    def _host(self, request: Request) -> str:
        if self.cfg.https.tls_mode == "behind-proxy":
            fwd = request.headers.get("x-forwarded-host")
            if fwd:
                return fwd.split(",")[0].strip().split(":")[0].lower()
        return (request.headers.get("host") or "").split(":")[0].lower()

    def _is_https(self, request: Request) -> bool:
        if self.cfg.https.tls_mode == "behind-proxy":
            return request.headers.get("x-forwarded-proto", "").lower() == "https"
        return request.url.scheme == "https"

    def _client_ip(self, request: Request) -> str:
        if self.cfg.https.tls_mode == "behind-proxy":
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else ""

    # ---- static -----------------------------------------------------------------------------
    async def static(self, request: Request) -> Response:
        started = time.monotonic()
        host = self._host(request)
        if self.cfg.studio.enabled and host == self.cfg.studio.hostname.lower():
            return await self._proxy_studio(request)
        site = self.table.get(host)
        response: Response
        if site is None:
            response = PlainTextResponse("No site is served at this address.\n", status_code=404)
        else:
            response = await self._serve(request, host, site)
        await self._event(request, site or "", response, started)
        return response

    async def _serve(self, request: Request, host: str, site: str) -> Response:
        canon = self.canonical.get(host, host)
        path = request.url.path
        if host != canon:
            return RedirectResponse(f"https://{canon}{path}", status_code=301)
        if (
            not self._is_https(request)
            and self.cfg.https.enabled
            and self.cfg.https.tls_mode != "behind-proxy"
        ):
            return RedirectResponse(f"https://{host}{path}", status_code=301)
        for rule in self.redirects.get(site, []):
            if rule.get("from") == path:
                return RedirectResponse(
                    str(rule.get("to", "/")), status_code=int(rule.get("status", 301))
                )
        root = self.store.site(site).current_dir("web")
        if root is None:
            return PlainTextResponse("This site has no release yet.\n", status_code=503)
        segments = [s for s in path.split("/") if s]
        if any(s in ("..", ".") or s.startswith(".") for s in segments):
            return self._not_found(root)
        target = root.joinpath(*segments) if segments else root
        try:
            resolved = target.resolve()
            resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            return self._not_found(root)
        if resolved.is_dir():
            if not path.endswith("/"):
                return RedirectResponse(path + "/", status_code=301)
            resolved = resolved / "index.html"
        if not resolved.is_file():
            return self._not_found(root)
        resp = FileResponse(str(resolved), headers=self._file_headers(site, resolved))
        return resp

    async def _proxy_studio(self, request: Request) -> Response:
        """Forward a request to the local studio, with the headers its remote mode expects."""
        import httpx

        if not self._is_https(request):
            return RedirectResponse(
                f"https://{self.cfg.studio.hostname}{request.url.path}", status_code=301
            )
        upstream = self.cfg.studio.upstream.rstrip("/")
        url = upstream + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        skip = {"host", "connection", "content-length", "transfer-encoding"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
        headers["X-Forwarded-For"] = self._client_ip(request)
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = self.cfg.studio.hostname
        headers["Host"] = self.cfg.studio.hostname
        body = await request.body()
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            try:
                r = await client.request(request.method, url, headers=headers, content=body)
            except httpx.HTTPError:
                return PlainTextResponse(
                    "The studio is not running on this server.\n", status_code=502
                )
        out_headers = {
            k: v
            for k, v in r.headers.items()
            if k.lower()
            not in ("content-encoding", "transfer-encoding", "connection", "content-length")
        }
        return Response(r.content, status_code=r.status_code, headers=out_headers)

    def _not_found(self, root: Path) -> Response:
        page = root / "404.html"
        if page.is_file():
            return FileResponse(str(page), status_code=404, media_type="text/html")
        return PlainTextResponse("Not found\n", status_code=404)

    def _file_headers(self, site: str, path: Path) -> dict[str, str]:
        h = dict(SECURITY_HEADERS)
        h.update(self.headers.get(site, {}))
        if path.suffix.lower() in (
            ".css",
            ".js",
            ".woff2",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".svg",
            ".ico",
            ".gif",
        ):
            h["Cache-Control"] = "public, max-age=604800"
        else:
            h["Cache-Control"] = "public, max-age=300"
        return h

    async def _event(self, request: Request, site: str, response: Response, started: float) -> None:
        if self.on_event is None:
            return
        try:
            result = self.on_event(
                {
                    "platform": "web",
                    "site": site,
                    "url": request.url.path,
                    "status": response.status_code,
                    "bytes": int(response.headers.get("content-length", "0") or 0),
                    "ip": self._client_ip(request),
                    "referrer": request.headers.get("referer"),
                    "user_agent": request.headers.get("user-agent"),
                    "tls_version": "proxy" if self.cfg.https.tls_mode == "behind-proxy" else None,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "host": self._host(request),
                }
            )
            if result is not None:
                await result
        except Exception:  # noqa: BLE001
            log.exception("analytics sink failed")

    # ---- API plumbing -----------------------------------------------------------------------
    def _api_guard(self, request: Request) -> Response | None:
        if not self._is_https(request):
            return JSONResponse({"error": "the API is served only over HTTPS"}, status_code=403)
        if self.cfg.admin.hostname and self._host(request) != self.cfg.admin.hostname.lower():
            return JSONResponse({"error": "not the admin hostname"}, status_code=404)
        ip = self._client_ip(request)
        now = time.time()
        bucket = [t for t in self._rate.get(ip, []) if now - t < 60]
        if len(bucket) >= self.cfg.limits.rate_limit_per_minute:
            return JSONResponse({"error": "rate limited"}, status_code=429)
        bucket.append(now)
        self._rate[ip] = bucket
        return None

    def _bearer(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _admin_ok(self, request: Request) -> bool:
        raw = self._bearer(request)
        return (
            bool(raw)
            and bool(self.cfg.admin.token_hash)
            and verify_admin_token(self.cfg.admin.token_hash, raw)
        )

    def _site_ok(self, request: Request, site: str) -> bool:
        raw = self._bearer(request)
        if not raw:
            return False
        if self._admin_ok(request):
            return True
        return self.store.site(site).verify_token(raw)

    async def healthz(self, request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    async def status(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        sites = []
        for sid in self.store.sites():
            s = self.store.site(sid)
            sites.append(
                {
                    "site": sid,
                    "release": s.current(),
                    "hostnames": s.site_config().get("hostnames", {}),
                }
            )
        return JSONResponse(
            {"version": __version__, "tls_mode": self.cfg.https.tls_mode, "sites": sites}
        )

    async def sites(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"sites": self.store.sites()})

    async def manifest(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site = request.path_params["site"]
        if not self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        m = self.store.site(site).current_manifest() if self.store.site(site).exists() else None
        if m is None:
            return JSONResponse({"error": "no release"}, status_code=404)
        return JSONResponse(m)

    async def releases(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site = request.path_params["site"]
        if not self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        store = self.store.site(site)
        if request.method == "GET":
            return JSONResponse(
                {
                    "releases": [dataclasses.asdict(r) for r in store.list_releases()],
                    "current": store.current(),
                }
            )
        body = await request.body()
        if len(body) > MAX_MANIFEST:
            return JSONResponse({"error": "manifest too large"}, status_code=413)
        try:
            import json

            manifest = json.loads(body)
            if request.query_params.get("dry_run"):
                from constellation.server.releases import release_id_for, validate_manifest

                validate_manifest(manifest, max_bytes=self.cfg.limits.max_release_bytes)
                missing = sorted(
                    {
                        e["sha256"]
                        for files in manifest["platforms"].values()
                        for e in files.values()
                        if not store.has_blob(e["sha256"])
                    }
                )
                return JSONResponse(
                    {"release_id": release_id_for(manifest), "missing": missing, "dry_run": True}
                )
            rid, missing = store.stage(manifest, max_bytes=self.cfg.limits.max_release_bytes)
        except (ReleaseError, ValueError, KeyError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"release_id": rid, "missing": missing})

    async def put_blob(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site, sha = request.path_params["site"], request.path_params["sha"]
        if not self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        length = int(request.headers.get("content-length", "0") or 0)
        if length > MAX_BLOB:
            return JSONResponse({"error": "blob too large"}, status_code=413)
        data = await request.body()
        if len(data) > MAX_BLOB:
            return JSONResponse({"error": "blob too large"}, status_code=413)
        if hashlib.sha256(data).hexdigest() != sha:
            return JSONResponse({"error": "hash mismatch"}, status_code=409)
        self.store.site(site).put_blob(sha, data)
        return JSONResponse({"ok": True}, status_code=201)

    async def commit(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site, rid = request.path_params["site"], request.path_params["rid"]
        if not self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        store = self.store.site(site)
        try:
            store.commit(rid)
        except ReleaseError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        pruned = store.prune(self.cfg.limits.keep_releases)
        self.reload()
        if self.on_release:
            self.on_release(site)
        return JSONResponse({"status": "active", "release_id": rid, "pruned": pruned})

    async def activate(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site, rid = request.path_params["site"], request.path_params["rid"]
        if not self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            self.store.site(site).activate(rid)
        except ReleaseError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        self.reload()
        if self.on_release:
            self.on_release(site)
        return JSONResponse({"status": "active", "release_id": rid})

    async def tokens(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        site = request.path_params["site"]
        try:
            store = self.store.site(site)
        except ReleaseError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if request.method == "GET":
            return JSONResponse({"tokens": store.list_tokens()})
        body = await request.json() if int(request.headers.get("content-length", "0") or 0) else {}
        tid, raw = store.mint_token(str(body.get("label", "studio")))
        return JSONResponse({"id": tid, "token": raw}, status_code=201)

    async def revoke_token(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        ok = self.store.site(request.path_params["site"]).revoke_token(request.path_params["tid"])
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    async def retire_site(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self.store.site(request.path_params["site"]).retire()
        self.reload()
        if self.on_release:
            self.on_release(request.path_params["site"])
        return JSONResponse({"ok": True})
