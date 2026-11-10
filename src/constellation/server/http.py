"""The HTTP side: static files from the active release per hostname, plus the publish and admin
API under ``/_constellation/`` on the admin hostname."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import re
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

STUDIO_MAX_BODY = 30 * 1024 * 1024
log = logging.getLogger("constellation.server.http")


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or None once it passes ``limit`` (chunked bodies included)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            return None
    return bytes(buf)


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
        self.analytics: Any = None
        self.challenges: Any = None
        self.server: Any = None
        self.table: dict[str, str] = {}
        self.canonical: dict[str, str] = {}
        self.redirects: dict[str, list[dict[str, Any]]] = {}
        self.headers: dict[str, dict[str, str]] = {}
        self.site_lang: dict[str, str] = {}
        self._rate: dict[str, list[float]] = {}
        self._auth_ok: dict[str, float] = {}
        self._verify_slots: asyncio.Semaphore | None = None
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
                Route(
                    "/_constellation/sites/{site}/analytics/{report}",
                    self.analytics_report,
                    methods=["GET"],
                ),
                Route("/_constellation/server/config", self.server_config, methods=["GET", "PUT"]),
                Route("/_constellation/server/certs", self.server_certs, methods=["GET"]),
                Route(
                    "/_constellation/server/certs/{platform}/{host}/regenerate",
                    self.regenerate_cert,
                    methods=["POST"],
                ),
                Route("/.well-known/acme-challenge/{token}", self.acme_challenge, methods=["GET"]),
                Route(
                    "/{path:path}",
                    self.static,
                    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                ),
            ],
            exception_handlers={ReleaseError: self._release_error},
        )

    # ---- routing tables ------------------------------------------------------------------
    def reload(self) -> None:
        table = self.store.hostname_table().get("web", {})
        canonical: dict[str, str] = {}
        redirects: dict[str, list[dict[str, Any]]] = {}
        headers: dict[str, dict[str, str]] = {}
        for sid in self.store.sites():
            try:
                cfg = self.store.site(sid).site_config()
                hosts = [str(h).lower() for h in cfg.get("hostnames", {}).get("web", [])]
                canon = str(
                    cfg.get("canonical", {}).get("web") or (hosts[0] if hosts else "")
                ).lower()
                site_redirects = list(cfg.get("redirects", []))
                site_headers = {
                    str(k): str(v) for k, v in cfg.get("headers", {}).get("web", {}).items()
                }
                lang = str(cfg.get("language", "en"))
            except (AttributeError, TypeError, ValueError):
                # One site's bad config must not take every other site down.
                log.exception("ignoring the server config of site %s", sid)
                continue
            for h in hosts:
                canonical[h] = canon
            redirects[sid] = site_redirects
            headers[sid] = site_headers
            self.site_lang[sid] = lang
        self.table, self.canonical, self.redirects, self.headers = (
            table,
            canonical,
            redirects,
            headers,
        )

    def _host(self, request: Request) -> str:
        raw = request.headers.get("host") or ""
        if self.cfg.https.tls_mode == "behind-proxy":
            fwd = request.headers.get("x-forwarded-host")
            if fwd:
                raw = fwd.split(",")[-1].strip()
        return raw.split(":")[0].lower().rstrip(".")

    def _is_https(self, request: Request) -> bool:
        if self.cfg.https.tls_mode == "behind-proxy":
            return request.headers.get("x-forwarded-proto", "").lower() == "https"
        return request.url.scheme == "https"

    def _client_ip(self, request: Request) -> str:
        if self.cfg.https.tls_mode == "behind-proxy":
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                # The proxy appends the address it saw; entries to its left are whatever the
                # client chose to send.
                return fwd.split(",")[-1].strip()
        return request.client.host if request.client else ""

    # ---- static -----------------------------------------------------------------------------
    async def static(self, request: Request) -> Response:
        started = time.monotonic()
        host = self._host(request)
        if self.cfg.studio.enabled and host == self.cfg.studio.hostname.lower():
            return await self._proxy_studio(request)
        if request.method not in ("GET", "HEAD"):
            return PlainTextResponse("Method not allowed.\n", status_code=405)
        site = self.table.get(host)
        response: Response
        if site is None:
            if request.url.path.startswith("/.well-known/acme-challenge/"):
                return await self.acme_challenge(request)
            response = PlainTextResponse("No site is served at this address.\n", status_code=404)
        else:
            response = await self._serve(request, host, site)
        await self._event(request, site or "", response, started)
        return response

    async def _serve(self, request: Request, host: str, site: str) -> Response:
        canon = self.canonical.get(host, host)
        path = request.url.path
        query = f"?{request.url.query}" if request.url.query else ""
        if path.startswith("/.well-known/acme-challenge/"):
            return await self.acme_challenge(request)
        port = "" if self.cfg.https.port == 443 else f":{self.cfg.https.port}"
        if host != canon:
            return RedirectResponse(f"https://{canon}{port}{path}{query}", status_code=301)
        if (
            not self._is_https(request)
            and self.cfg.https.enabled
            and self.cfg.https.tls_mode != "behind-proxy"
        ):
            return RedirectResponse(f"https://{host}{port}{path}{query}", status_code=301)
        if "//" in path:
            # "//about" would otherwise become a protocol-relative redirect to host "about".
            return RedirectResponse(re.sub(r"/{2,}", "/", path) + query, status_code=301)
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
                return RedirectResponse(path + "/" + query, status_code=301)
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
        body = await _read_capped(request, STUDIO_MAX_BODY)
        if body is None:
            return PlainTextResponse("Request too large.\n", status_code=413)
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

    async def _release_error(self, request: Request, exc: Exception) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=400)

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
        if len(self._rate) > 10_000:
            self._rate = {k: v for k, v in self._rate.items() if v and now - v[-1] < 60}
        bucket = [t for t in self._rate.get(ip, []) if now - t < 60]
        if len(bucket) >= self.cfg.limits.rate_limit_per_minute:
            return JSONResponse({"error": "rate limited"}, status_code=429)
        bucket.append(now)
        self._rate[ip] = bucket
        return None

    def _hostname_problem(self, site: str, site_config: Any) -> str:
        """A release may only claim hostnames no other site serves, and never the server's
        own admin or studio hostname."""
        if not isinstance(site_config, dict):
            return ""
        reserved = {h.lower() for h in (self.cfg.admin.hostname, self.cfg.studio.hostname) if h}
        hostnames = site_config.get("hostnames", {})
        for hosts in hostnames.values() if isinstance(hostnames, dict) else []:
            for h in hosts if isinstance(hosts, list) else []:
                if str(h).lower() in reserved:
                    return f"{h} is this server's own hostname and cannot be used by a site"
        clashes = self.store.hostname_conflicts(site, site_config)
        if clashes:
            return "hostnames already used by another site: " + ", ".join(clashes)
        return ""

    def _bearer(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    async def _verified(self, raw: str, scope: str, check: Callable[[], bool]) -> bool:
        """Run an Argon2 token check off the event loop, at most two at a time, and remember
        tokens that passed so a publish's many blob uploads verify once."""
        key = hashlib.sha256(f"{scope}\0{raw}".encode()).hexdigest()
        now = time.monotonic()
        if self._auth_ok.get(key, 0.0) > now:
            return True
        if self._verify_slots is None:
            self._verify_slots = asyncio.Semaphore(2)
        async with self._verify_slots:
            ok = await asyncio.to_thread(check)
        if ok:
            if len(self._auth_ok) > 1000:
                self._auth_ok = {k: t for k, t in self._auth_ok.items() if t > now}
            self._auth_ok[key] = now + 600
        return ok

    def forget_tokens(self) -> None:
        """Drop remembered tokens after any change to who may publish."""
        self._auth_ok.clear()

    async def _admin_ok(self, request: Request) -> bool:
        raw = self._bearer(request)
        hashed = self.cfg.admin.token_hash
        if not raw or not hashed or not raw.startswith("cst_admin_"):
            return False
        return await self._verified(raw, "admin", lambda: verify_admin_token(hashed, raw))

    async def _site_ok(self, request: Request, site: str) -> bool:
        raw = self._bearer(request)
        if not raw:
            return False
        if raw.startswith("cst_admin_"):
            return await self._admin_ok(request)
        try:
            store = self.store.site(site)
        except ReleaseError:
            return False
        return await self._verified(raw, f"site:{site}", lambda: store.verify_token(raw))

    async def acme_challenge(self, request: Request) -> Response:
        token = request.path_params["token"]
        value = self.challenges.get(token) if self.challenges is not None else None
        if value is None:
            return PlainTextResponse("not found\n", status_code=404)
        return PlainTextResponse(value)

    EDITABLE = {
        "https.tls_mode": str,
        "https.acme_email": str,
        "https.acme_directory": str,
        "https.cert_dir": str,
        "https.plain_port": int,
        "gemini.enabled": bool,
        "gemini.port": int,
        "gemini.cert_years": int,
        "limits.keep_releases": int,
        "limits.max_connections": int,
        "limits.rate_limit_per_minute": int,
        "analytics.enabled": bool,
        "analytics.ip_policy": str,
        "analytics.retention_days": int,
        "studio.enabled": bool,
        "studio.hostname": str,
        "studio.upstream": str,
        "studio.state_dir": str,
        "http.port": int,
        "https.port": int,
    }
    RESTART_KEYS = {
        "https.tls_mode",
        "https.plain_port",
        "gemini.enabled",
        "gemini.port",
        "http.port",
        "https.port",
        "analytics.enabled",
    }

    async def server_config(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.method == "GET":
            data = self.cfg.model_dump()
            data["admin"].pop("token_hash", None)
            return JSONResponse(
                {
                    "config": data,
                    "editable": sorted(self.EDITABLE),
                    "restart_required": sorted(self.RESTART_KEYS),
                }
            )
        body = await request.json()
        changes = body.get("changes", {}) if isinstance(body, dict) else {}
        restart = False
        try:
            from constellation.server.config import ServerConfig, save_server_config

            data = self.cfg.model_dump()
            for dotted, value in changes.items():
                if dotted not in self.EDITABLE:
                    return JSONResponse({"error": f"{dotted} is not editable"}, status_code=400)
                caster = self.EDITABLE[dotted]
                section, key = dotted.split(".")
                data[section][key] = (
                    (value in (True, "true", "on", "1", 1)) if caster is bool else caster(value)
                )
                if dotted in self.RESTART_KEYS:
                    restart = True
            new_cfg = ServerConfig.model_validate(data)
        except (ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        for section in ("http", "https", "gemini", "gopher", "limits", "analytics", "studio"):
            setattr(self.cfg, section, getattr(new_cfg, section))
        save_server_config(self.cfg.data / "server.toml", self.cfg)
        self.reload()
        if self.server is not None:
            self.server.reload_gemini()
        return JSONResponse({"ok": True, "restart_required": restart})

    async def server_certs(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from constellation.server.tls import cert_dir, expiry, fingerprint

        out: list[dict[str, Any]] = []
        table = self.store.hostname_table()
        for platform in ("gemini", "web"):
            hosts = set(table.get(platform, {}))
            if platform == "web" and self.cfg.admin.hostname:
                hosts.add(self.cfg.admin.hostname)
            for host in sorted(hosts):
                d = cert_dir(self.cfg.data, platform, host)
                cert = d / ("cert.pem" if platform == "gemini" else "fullchain.pem")
                if not cert.is_file() and platform == "web":
                    cert = d / "cert.pem"
                if cert.is_file():
                    try:
                        out.append(
                            {
                                "platform": platform,
                                "hostname": host,
                                "fingerprint": fingerprint(cert),
                                "expires": expiry(cert).isoformat(),
                                "kind": "acme" if cert.name == "fullchain.pem" else "self-signed",
                            }
                        )
                    except Exception as e:  # noqa: BLE001
                        out.append({"platform": platform, "hostname": host, "error": str(e)})
                    continue
                out.append({"platform": platform, "hostname": host, "kind": "none"})
        acme = (
            self.server.acme.report()
            if self.server is not None and getattr(self.server, "acme", None)
            else []
        )
        return JSONResponse(
            {"certificates": out, "acme": acme, "tls_mode": self.cfg.https.tls_mode}
        )

    async def regenerate_cert(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        platform, host = request.path_params["platform"], request.path_params["host"].lower()
        if platform != "gemini":
            return JSONResponse(
                {
                    "error": "only Gemini certificates are regenerated here; HTTPS ones come from ACME"
                },
                status_code=400,
            )
        from constellation.server.tls import fingerprint, regenerate

        _key, cert = regenerate(self.cfg.data, "gemini", host, years=self.cfg.gemini.cert_years)
        if self.server is not None:
            self.server.reload_gemini()
        return JSONResponse({"ok": True, "fingerprint": fingerprint(cert)})

    async def analytics_report(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site, report = request.path_params["site"], request.path_params["report"]
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if self.analytics is None:
            return JSONResponse({"error": "analytics are disabled on this server"}, status_code=404)
        try:
            days = max(1, min(365, int(request.query_params.get("days", "30"))))
        except ValueError:
            days = 30
        a = self.analytics
        if report == "overview":
            return JSONResponse(a.overview(site, days))
        if report == "content":
            return JSONResponse({"items": a.content(site, days)})
        if report == "technical":
            return JSONResponse(a.technical(site, days))
        if report == "referrers":
            return JSONResponse({"items": a.referrers(site, days)})
        if report == "export.csv":
            return PlainTextResponse(a.export_csv(site, days), media_type="text/csv")
        return JSONResponse({"error": "unknown report"}, status_code=404)

    async def healthz(self, request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    async def status(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
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
        studio: dict[str, Any] = {}
        if self.cfg.studio.enabled and self.cfg.studio.hostname:
            from constellation.studio import setup_code

            url = f"https://{self.cfg.studio.hostname}"
            studio = {"url": url, "setup_link": ""}
            if self.cfg.studio.state_dir:
                code = setup_code.read(Path(self.cfg.studio.state_dir))
                if code:
                    studio["setup_link"] = setup_code.setup_link(url, code)
        return JSONResponse(
            {
                "version": __version__,
                "tls_mode": self.cfg.https.tls_mode,
                "sites": sites,
                "studio": studio,
            }
        )

    async def sites(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"sites": self.store.sites()})

    async def manifest(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site = request.path_params["site"]
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        m = self.store.site(site).current_manifest() if self.store.site(site).exists() else None
        if m is None:
            return JSONResponse({"error": "no release"}, status_code=404)
        return JSONResponse(m)

    async def releases(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site = request.path_params["site"]
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        store = self.store.site(site)
        if request.method == "GET":
            return JSONResponse(
                {
                    "releases": [dataclasses.asdict(r) for r in store.list_releases()],
                    "current": store.current(),
                }
            )
        body = await _read_capped(request, MAX_MANIFEST)
        if body is None:
            return JSONResponse({"error": "manifest too large"}, status_code=413)
        try:
            import json

            manifest = json.loads(body)
            from constellation.server.releases import release_id_for, validate_manifest

            validate_manifest(manifest, max_bytes=self.cfg.limits.max_release_bytes)
            clash = self._hostname_problem(site, manifest.get("site_config", {}))
            if clash:
                return JSONResponse({"error": clash}, status_code=409)
            if request.query_params.get("dry_run"):
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
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        length = int(request.headers.get("content-length", "0") or 0)
        if length > MAX_BLOB:
            return JSONResponse({"error": "blob too large"}, status_code=413)
        data = await _read_capped(request, MAX_BLOB)
        if data is None:
            return JSONResponse({"error": "blob too large"}, status_code=413)
        if hashlib.sha256(data).hexdigest() != sha:
            return JSONResponse({"error": "hash mismatch"}, status_code=409)
        self.store.site(site).put_blob(sha, data)
        return JSONResponse({"ok": True}, status_code=201)

    async def commit(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        site, rid = request.path_params["site"], request.path_params["rid"]
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        store = self.store.site(site)
        try:
            clash = self._hostname_problem(site, store.manifest(rid).get("site_config", {}))
            if clash:
                return JSONResponse({"error": clash}, status_code=409)
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
        if not await self._site_ok(request, site):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            store = self.store.site(site)
            clash = self._hostname_problem(site, store.manifest(rid).get("site_config", {}))
            if clash:
                return JSONResponse({"error": clash}, status_code=409)
            store.activate(rid)
        except ReleaseError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        self.reload()
        if self.on_release:
            self.on_release(site)
        return JSONResponse({"status": "active", "release_id": rid})

    async def tokens(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
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
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        ok = self.store.site(request.path_params["site"]).revoke_token(request.path_params["tid"])
        self.forget_tokens()
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    async def retire_site(self, request: Request) -> Response:
        if (g := self._api_guard(request)) is not None:
            return g
        if not await self._admin_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self.store.site(request.path_params["site"]).retire()
        self.forget_tokens()
        self.reload()
        if self.on_release:
            self.on_release(request.path_params["site"])
        return JSONResponse({"ok": True})
