"""The studio web application (FastAPI + Jinja2 + htmx)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import tomlkit
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from constellation import __version__
from constellation.core.build.pipeline import media_type_for
from constellation.core.content import ContentError, load_site
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import RenderGemini
from constellation.core.render.gemtext import GemtextRenderer
from constellation.core.render.html import HtmlRenderer
from constellation.core.routes import RefResolver, url_for
from constellation.core.themes import BUILTIN_DIR, load_theme, theme_chain
from constellation.studio import content_ops, locks
from constellation.studio.auth import hash_password, is_loopback, verify_password
from constellation.studio.jobs import Jobs
from constellation.studio.preview import PreviewBuilder, gemtext_to_html
from constellation.studio.state import State
from constellation.studio.workspace import (
    Workspace,
    atomic_write,
    create_site,
    load_workspace,
    read_site_toml,
    set_path,
    write_site_toml,
)

HERE = Path(__file__).resolve().parent
SESSION_COOKIE = "constellation_session"


class Studio:
    """Everything the routes need; one per process."""

    def __init__(self, state: State, workspace_root: Path) -> None:
        self.state = state
        self.workspace_root = Path(workspace_root)
        self.jobs = Jobs()
        self.previews = PreviewBuilder(theme_search=[self.workspace_root / "themes"])
        self._heartbeats: set[str] = set()

    @property
    def workspace(self) -> Workspace:
        return load_workspace(self.workspace_root)

    def site_root(self, site_id: str) -> Path:
        ws = self.workspace
        entry = ws.site(site_id)
        if entry is None:
            raise HTTPException(404, f"no site {site_id!r}")
        return ws.site_dir(site_id)

    def needs_setup(self) -> bool:
        return not self.state.settings.password_hash

    def read_only_reason(self, site_id: str) -> str:
        root = self.site_root(site_id)
        lock = locks.held_elsewhere(root, self.state.settings.machine_id)
        if lock is not None:
            return f"open on {lock.label or lock.machine_id}; take over if that studio is closed"
        return ""

    def touch_lock(self, site_id: str) -> None:
        root = self.site_root(site_id)
        if not locks.held_elsewhere(root, self.state.settings.machine_id):
            locks.write_lock(root, self.state.settings.machine_id, locks.machine_label())
            self._heartbeats.add(site_id)

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            for site_id in list(self._heartbeats):
                with contextlib.suppress(Exception):
                    self.touch_lock(site_id)

    def release_all(self) -> None:
        for site_id in list(self._heartbeats):
            with contextlib.suppress(Exception):
                locks.release_lock(self.site_root(site_id), self.state.settings.machine_id)


def create_app(state: State, workspace_root: Path) -> FastAPI:
    studio = Studio(state, workspace_root)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.get_running_loop().create_task(studio.heartbeat_loop())
        try:
            yield
        finally:
            task.cancel()
            studio.release_all()

    app = FastAPI(
        title="ConstellationCMS studio",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.studio = studio
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update({"version": __version__})
    templates.env.filters["tojson_attr"] = lambda v: json.dumps(v)

    # ---- auth plumbing --------------------------------------------------------------------
    def client_host(request: Request) -> str:
        s = studio.state.settings
        if s.trust_proxy:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else ""

    def session_of(request: Request) -> dict[str, Any] | None:
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            sess = studio.state.touch_session(sid)
            if sess:
                return sess
        s = studio.state.settings
        if (
            s.effective_auto_login()
            and is_loopback(request.client.host if request.client else None)
            and not s.trust_proxy
        ):
            return {"id": "local", "csrf": _local_csrf(studio), "auto": True}
        return None

    def require_session(request: Request) -> dict[str, Any]:
        if studio.needs_setup():
            raise HTTPException(status_code=307, headers={"Location": "/setup"})
        sess = session_of(request)
        if sess is None:
            if request.headers.get("hx-request"):
                raise HTTPException(401, "signed out", headers={"HX-Redirect": "/login"})
            raise HTTPException(
                status_code=307, headers={"Location": f"/login?next={request.url.path}"}
            )
        request.state.session = sess
        return sess

    async def require_csrf(
        request: Request, sess: dict[str, Any] = Depends(require_session)
    ) -> dict[str, Any]:
        """Mutating requests carry the session's CSRF token in a header (htmx, fetch) or a form field."""
        token = request.headers.get("x-csrf-token")
        if token is None and request.headers.get("content-type", "").startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")
        ):
            form = await request.form()
            token = str(form.get("csrf", "")) or None
        if not token or not secrets.compare_digest(token, str(sess["csrf"])):
            raise HTTPException(403, "bad or missing CSRF token")
        return sess

    class SecurityHeaders(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            response = await call_next(request)
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            if request.url.path.startswith("/sites/") and "/preview/" in request.url.path:
                response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
            else:
                response.headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src 'self'; frame-ancestors 'none'; form-action 'self'",
                )
            return response

    app.add_middleware(SecurityHeaders)

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        sess = getattr(request.state, "session", None)
        ctx.setdefault("csrf", sess["csrf"] if sess else "")
        ctx.setdefault("workspace", studio.workspace)
        ctx.setdefault("settings", studio.state.settings)
        return cast(HTMLResponse, templates.TemplateResponse(request, name, ctx))

    # ---- setup, login, logout -------------------------------------------------------------
    @app.get("/setup", response_class=HTMLResponse)
    async def setup_form(request: Request) -> Response:
        if not studio.needs_setup():
            return RedirectResponse("/", 303)
        return render(request, "setup.html", error="")

    @app.post("/setup")
    async def setup_submit(
        request: Request, password: str = Form(...), confirm: str = Form(...)
    ) -> Response:
        if not studio.needs_setup():
            return RedirectResponse("/", 303)
        if len(password) < 10:
            return render(request, "setup.html", error="Use at least 10 characters.")
        if password != confirm:
            return render(request, "setup.html", error="The two passwords differ.")
        studio.state.settings.password_hash = hash_password(password)
        studio.state.save_settings()
        studio.state.audit("setup", remote=client_host(request))
        return _login_response(studio, request, "/")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/") -> Response:
        if studio.needs_setup():
            return RedirectResponse("/setup", 303)
        if session_of(request):
            return RedirectResponse(next if next.startswith("/") else "/", 303)
        return render(request, "login.html", error="", next=next)

    @app.post("/login")
    async def login_submit(
        request: Request, password: str = Form(...), next: str = Form("/")
    ) -> Response:
        host = client_host(request)
        locked = studio.state.login_locked(host)
        if locked:
            studio.state.audit("login_locked", remote=host)
            return render(
                request,
                "login.html",
                error=f"Too many failures; try again in {int(locked // 60) + 1} minutes.",
                next=next,
            )
        if not verify_password(studio.state.settings.password_hash, password):
            studio.state.login_failed(host)
            studio.state.audit("login_failed", remote=host)
            return render(request, "login.html", error="Wrong password.", next=next)
        studio.state.login_succeeded(host)
        studio.state.audit("login", remote=host)
        return _login_response(studio, request, next if next.startswith("/") else "/")

    @app.post("/logout")
    async def logout(request: Request, sess: dict[str, Any] = Depends(require_csrf)) -> Response:
        if sess.get("id") != "local":
            studio.state.delete_session(str(sess["id"]))
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---- sites ----------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def sites_page(
        request: Request, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        return render(request, "sites.html", error="")

    @app.post("/sites")
    async def sites_create(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        name: str = Form(...),
        web_url: str = Form(""),
        gemini_url: str = Form(""),
        timezone: str = Form("UTC"),
        author: str = Form(""),
    ) -> Response:
        try:
            entry = create_site(
                studio.workspace,
                name.strip(),
                web_url=web_url.strip(),
                gemini_url=gemini_url.strip(),
                timezone=timezone.strip() or "UTC",
                author=author.strip(),
            )
        except ValueError as e:
            return render(request, "sites.html", error=str(e))
        studio.state.audit("site_created", entry.id, client_host(request))
        return RedirectResponse(f"/sites/{entry.id}", 303)

    @app.get("/sites/{site_id}", response_class=HTMLResponse)
    async def dashboard(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        studio.touch_lock(site_id)
        posts = content_ops.list_items(root, "post")
        problems: list[str] = []
        warnings: dict[str, int] = {}
        build, problems = studio.previews.try_get(site_id, root)
        if build:
            for pb in build.platforms.values():
                for w in pb.warnings:
                    warnings[w.severity] = warnings.get(w.severity, 0) + 1
        return render(
            request,
            "dashboard.html",
            site_id=site_id,
            posts=posts[:8],
            drafts=[p for p in posts if p.get("status") == "draft"],
            conflicts=content_ops.find_conflict_files(root),
            problems=problems,
            warnings=warnings,
            read_only=studio.read_only_reason(site_id),
            jobs=studio.jobs.for_site(site_id, 5),
            releases=_releases(root),
        )

    @app.post("/sites/{site_id}/take-over")
    async def take_over(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        locks.take_over(studio.site_root(site_id), studio.state.settings.machine_id)
        studio.touch_lock(site_id)
        return RedirectResponse(f"/sites/{site_id}", 303)

    # ---- content lists and editor ----------------------------------------------------------
    @app.get("/sites/{site_id}/posts", response_class=HTMLResponse)
    async def posts_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        status: str = "",
        tag: str = "",
    ) -> Response:
        return await items_page(request, site_id, "post", sess, status, tag)

    @app.get("/sites/{site_id}/pages", response_class=HTMLResponse)
    async def pages_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        status: str = "",
        tag: str = "",
    ) -> Response:
        return await items_page(request, site_id, "page", sess, status, tag)

    async def items_page(
        request: Request, site_id: str, kind: str, sess: dict[str, Any], status: str, tag: str
    ) -> Response:
        root = studio.site_root(site_id)
        items = content_ops.list_items(root, kind)
        if status:
            items = [i for i in items if i.get("status") == status]
        if tag:
            items = [i for i in items if tag in i.get("tags", [])]
        return render(
            request,
            "items.html",
            site_id=site_id,
            kind=kind,
            items=items,
            status=status,
            tag=tag,
            read_only=studio.read_only_reason(site_id),
        )

    @app.post("/sites/{site_id}/new")
    async def new_item(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        kind: str = Form(...),
        title: str = Form(...),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        rel = content_ops.create_item(root, kind, title.strip() or "Untitled")
        studio.state.audit("created", rel, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/edit?rel={rel}", 303)

    @app.get("/sites/{site_id}/edit", response_class=HTMLResponse)
    async def editor(
        request: Request, site_id: str, rel: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        studio.touch_lock(site_id)
        try:
            item = content_ops.load_item(root, rel, state=studio.state, site_id=site_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e)) from e
        site = _try_load_site(root, site_id)
        authors = [a.id for a in site.config.authors] if site else []
        return render(
            request,
            "editor.html",
            site_id=site_id,
            item=item,
            rel=rel,
            authors=authors,
            meta_json=json.dumps(_meta_for_form(item.meta)),
            read_only=studio.read_only_reason(site_id),
            gemini_enabled=bool(site and site.config.platform("gemini").enabled),
        )

    @app.get("/sites/{site_id}/raw")
    async def raw_body(
        request: Request, site_id: str, rel: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        try:
            item = content_ops.load_item(root, rel)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e)) from e
        return Response(item.body, media_type="text/plain; charset=utf-8")

    @app.post("/sites/{site_id}/save")
    async def save(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        payload = await request.json()
        rel = str(payload["rel"])
        meta = _meta_from_form(payload.get("meta", {}))
        try:
            item = content_ops.save_item(
                root,
                rel,
                meta,
                str(payload.get("body", "")),
                expected_sha=payload.get("sha"),
                state=studio.state,
                site_id=site_id,
                force=bool(payload.get("force")),
            )
        except content_ops.ConflictError as e:
            return JSONResponse({"error": str(e), "conflict": True}, status_code=409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "sha": item.sha256, "rel": item.rel, "slug": item.slug})

    @app.post("/sites/{site_id}/render")
    async def render_preview(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        """Render unsaved editor content for one platform: fragment HTML plus warnings."""
        root = studio.site_root(site_id)
        payload = await request.json()
        platform = str(payload.get("platform", "gemini"))
        body = str(payload.get("body", ""))
        meta = _meta_from_form(payload.get("meta", {}))
        slug = str(meta.get("slug") or payload.get("slug") or "preview")
        site = _try_load_site(root, site_id)
        cfg = site.config if site else None
        resolver = RefResolver(
            cfg,
            post_slugs={p.slug for p in site.posts} if site else set(),
            page_slugs={p.slug for p in site.pages} if site else set(),
        )
        doc = parse_markdown(body, resolve=lambda h, i: resolver.resolve(h, is_image=i))
        from constellation.core.models import SiteConfig, SiteInfo

        cfg = cfg or SiteConfig(site=SiteInfo(title="Preview"))
        if platform == "gemini":
            res = GemtextRenderer(
                cfg.render.gemini if cfg else RenderGemini(),
                lambda r: url_for(r, "gemini", cfg, item_slug=slug),
            ).render(doc)
            title = str(meta.get("title", ""))
            html_out = gemtext_to_html((f"# {title}\n\n" if title else "") + res.text)
            text = res.text
        else:
            res = HtmlRenderer(lambda r: url_for(r, "web", cfg, item_slug=slug)).render(doc)
            html_out = res.text
            text = ""
        media_base = f"/sites/{site_id}/preview/web/media/posts/{slug}/"
        return JSONResponse(
            {
                "html": html_out,
                "text": text,
                "media_base": media_base,
                "warnings": [w.as_dict() for w in res.warnings],
            }
        )

    @app.post("/sites/{site_id}/upload")
    async def upload(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        rel: str = Form(...),
        file: UploadFile = Form(...),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        data = await file.read()
        try:
            if not rel.endswith("index.md"):
                rel = content_ops.convert_to_bundle(root, rel)
            name = content_ops.add_media(root, rel, file.filename or "upload", data)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "name": name, "rel": rel, "markdown": f"![]({name})"})

    @app.post("/sites/{site_id}/trash")
    async def trash(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        rel: str = Form(...),
        kind: str = Form("post"),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        content_ops.trash_item(root, rel, state=studio.state, site_id=site_id)
        studio.state.audit("trashed", rel, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/{kind}s", 303)

    @app.post("/sites/{site_id}/duplicate")
    async def duplicate(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        rel: str = Form(...),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        new_rel = content_ops.duplicate_item(root, rel)
        return RedirectResponse(f"/sites/{site_id}/edit?rel={new_rel}", 303)

    # ---- preview of the built site -----------------------------------------------------------
    @app.get("/sites/{site_id}/preview/{platform}/{path:path}")
    async def preview_file(
        request: Request,
        site_id: str,
        platform: str,
        path: str,
        sess: dict[str, Any] = Depends(require_session),
    ) -> Response:
        root = studio.site_root(site_id)
        build, problems = studio.previews.try_get(site_id, root)
        if build is None:
            return HTMLResponse("<pre>" + "\n".join(problems) + "</pre>", status_code=500)
        if platform not in build.platforms:
            raise HTTPException(404)
        files = build.platforms[platform].files
        key = path or ("index.html" if platform == "web" else "index.gmi")
        if key.endswith("/"):
            key += "index.html"
        if key not in files and platform == "web" and f"{key}/index.html" in files:
            key = f"{key}/index.html"
        if key not in files:
            raise HTTPException(404)
        f = files[key]
        data = f.bytes()
        if platform == "gemini" and f.media_type.startswith("text/gemini"):
            return render(
                request,
                "gemini_preview.html",
                site_id=site_id,
                body=gemtext_to_html(data.decode("utf-8")),
                path=key,
            )
        prefix = f"/sites/{site_id}/preview/{platform}"
        if f.media_type.startswith("text/html"):
            text = re.sub(
                r'((?:href|src|content)=")/(?!/)',
                lambda m: m.group(1) + prefix + "/",
                data.decode("utf-8"),
            )
            text = re.sub(
                r'(srcset=")([^"]*)',
                lambda m: (
                    m.group(1)
                    + m.group(2).replace(" /", " " + prefix + "/").replace('"/', '"' + prefix + "/")
                ),
                text,
            )
            text = text.replace('srcset="/', f'srcset="{prefix}/')
            text = re.sub(r"(url\()/(?!/)", lambda m: m.group(1) + prefix + "/", text)
            return HTMLResponse(text)
        if f.media_type.startswith("text/css"):
            text = re.sub(
                r"(url\(['\"]?)/(?!/)", lambda m: m.group(1) + prefix + "/", data.decode("utf-8")
            )
            return Response(text, media_type=f.media_type)
        return Response(data, media_type=f.media_type or media_type_for(key))

    # ---- taxonomies ---------------------------------------------------------------------------
    @app.get("/sites/{site_id}/taxonomies", response_class=HTMLResponse)
    async def taxonomies(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        site = _try_load_site(root, site_id)
        groups = []
        if site:
            for kind, terms in (
                ("tag", site.tags),
                ("category", site.categories),
                ("series", site.series),
            ):
                groups.append(
                    (
                        kind,
                        [
                            (t.slug, t.title, len(t.posts), t.meta.description)
                            for t in terms.values()
                        ],
                    )
                )
        return render(
            request,
            "taxonomies.html",
            site_id=site_id,
            groups=groups,
            read_only=studio.read_only_reason(site_id),
        )

    @app.post("/sites/{site_id}/taxonomies")
    async def taxonomy_save(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        kind: str = Form(...),
        slug: str = Form(...),
        title: str = Form(""),
        description: str = Form(""),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        folder = {"tag": "tags", "category": "categories", "series": "series"}.get(kind)
        if folder is None or not content_ops.SAFE_MEDIA.sub("", slug) == slug:
            raise HTTPException(400, "bad taxonomy")
        path = root / "content" / folder / f"{slug}.md"
        existing = ""
        if path.is_file():
            import frontmatter

            existing = frontmatter.loads(path.read_text(encoding="utf-8")).content
        atomic_write(
            path,
            content_ops.compose(
                {"title": title.strip(), "description": description.strip()}, existing
            ),
        )
        return RedirectResponse(f"/sites/{site_id}/taxonomies", 303)

    # ---- settings ---------------------------------------------------------------------------
    @app.get("/sites/{site_id}/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        saved: str = "",
    ) -> Response:
        root = studio.site_root(site_id)
        doc = read_site_toml(root)
        return render(
            request,
            "settings.html",
            site_id=site_id,
            doc=doc,
            toml_text=tomlkit.dumps(doc),
            saved=saved,
            read_only=studio.read_only_reason(site_id),
            error="",
        )

    @app.post("/sites/{site_id}/settings")
    async def settings_save(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        form = await request.form()
        doc = read_site_toml(root)
        if "toml_text" in form:
            try:
                new_doc = tomlkit.parse(str(form["toml_text"]))
                from constellation.core.models import SiteConfig

                SiteConfig.model_validate(json.loads(json.dumps(new_doc.unwrap(), default=str)))
            except Exception as e:  # noqa: BLE001
                return render(
                    request,
                    "settings.html",
                    site_id=site_id,
                    doc=doc,
                    toml_text=str(form["toml_text"]),
                    saved="",
                    read_only="",
                    error=f"Not saved: {e}",
                )
            write_site_toml(root, new_doc)
        else:
            for key, value in form.multi_items():
                if key in ("csrf",) or not key.startswith("f."):
                    continue
                dotted = key[2:]
                set_path(doc, dotted, _coerce(dotted, str(value)))
            write_site_toml(root, doc)
        studio.state.audit("settings", site_id, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/settings?saved=1", 303)

    # ---- theme ------------------------------------------------------------------------------
    @app.get("/sites/{site_id}/theme", response_class=HTMLResponse)
    async def theme_page(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        doc = read_site_toml(root)
        current = str(doc.get("site", {}).get("theme", "base"))
        available = sorted(
            {p.name for p in BUILTIN_DIR.iterdir() if (p / "theme.toml").is_file()}
            | {
                p.name
                for p in (studio.workspace_root / "themes").glob("*")
                if (p / "theme.toml").is_file()
            }
        )
        theme = load_theme(current, search=[studio.workspace_root / "themes"])
        chain = theme_chain(theme, search=[studio.workspace_root / "themes"])
        settings_schema = [s for t in reversed(chain) for s in t.settings]
        values = dict(doc.get("theme", {}).get("settings", {})) if "theme" in doc else {}
        return render(
            request,
            "theme.html",
            site_id=site_id,
            current=current,
            available=available,
            theme=theme,
            schema=settings_schema,
            values=values,
            read_only=studio.read_only_reason(site_id),
        )

    @app.post("/sites/{site_id}/theme")
    async def theme_save(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        form = await request.form()
        doc = read_site_toml(root)
        set_path(doc, "site.theme", str(form.get("theme", "base")))
        theme = load_theme(
            str(form.get("theme", "base")), search=[studio.workspace_root / "themes"]
        )
        for t in theme_chain(theme, search=[studio.workspace_root / "themes"]):
            for s in t.settings:
                raw = form.get(f"s.{s.key}")
                if s.type == "bool":
                    set_path(doc, f"theme.settings.{s.key}", raw == "on")
                elif raw is not None:
                    set_path(doc, f"theme.settings.{s.key}", _coerce_type(s.type, str(raw)))
        write_site_toml(root, doc)
        return RedirectResponse(f"/sites/{site_id}/theme", 303)

    # ---- publish / export ------------------------------------------------------------------
    @app.get("/sites/{site_id}/publish", response_class=HTMLResponse)
    async def publish_page(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        doc = read_site_toml(root)
        exports = [dict(e) for e in doc.get("exports", [])]
        build, problems = studio.previews.try_get(site_id, root)
        errors = [
            w
            for pb in (build.platforms.values() if build else [])
            for w in pb.warnings
            if w.severity == "error"
        ]
        return render(
            request,
            "publish.html",
            site_id=site_id,
            exports=exports,
            problems=problems,
            errors=errors,
            jobs=studio.jobs.for_site(site_id),
            read_only=studio.read_only_reason(site_id),
            releases=_releases(root),
        )

    @app.post("/sites/{site_id}/exports")
    async def exports_save(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        name: str = Form(...),
        platform: str = Form(...),
        target: str = Form(...),
        layout: str = Form("plain"),
        delete: str = Form(""),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        doc = read_site_toml(root)
        exports = doc.get("exports")
        if exports is None:
            exports = tomlkit.aot()
            doc["exports"] = exports
        if delete:
            keep = [e for e in exports if e.get("name") != delete]
            new = tomlkit.aot()
            for e in keep:
                new.append(e)
            doc["exports"] = new
        else:
            t = tomlkit.table()
            t["name"], t["platform"], t["target"], t["layout"] = (
                name.strip(),
                platform,
                target.strip(),
                layout,
            )
            exports.append(t)
        write_site_toml(root, doc)
        return RedirectResponse(f"/sites/{site_id}/publish", 303)

    @app.post("/sites/{site_id}/export/{name}")
    async def export_run(
        request: Request,
        site_id: str,
        name: str,
        sess: dict[str, Any] = Depends(require_csrf),
        mode: str = Form("plan"),
    ) -> Response:
        root = studio.site_root(site_id)
        doc = read_site_toml(root)
        spec = next((dict(e) for e in doc.get("exports", []) if e.get("name") == name), None)
        if spec is None:
            raise HTTPException(404, "no such export")
        from constellation.studio.publish_ops import run_export

        try:
            job = await studio.jobs.start(
                "export",
                site_id,
                lambda job: run_export(job, root, site_id, spec, apply=mode == "apply"),
            )
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        studio.state.audit("export_" + mode, f"{site_id}:{name}", client_host(request))
        return render(request, "partials/job.html", job=job, site_id=site_id)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_status(
        request: Request, job_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        job = studio.jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        return render(request, "partials/job.html", job=job, site_id=job.site)

    # ---- account --------------------------------------------------------------------------
    @app.get("/account", response_class=HTMLResponse)
    async def account(
        request: Request, sess: dict[str, Any] = Depends(require_session), saved: str = ""
    ) -> Response:
        return render(
            request, "account.html", audit=studio.state.audit_tail(30), saved=saved, error=""
        )

    @app.post("/account/password")
    async def change_password(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        current: str = Form(...),
        password: str = Form(...),
        confirm: str = Form(...),
    ) -> Response:
        if not verify_password(studio.state.settings.password_hash, current):
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="Current password is wrong.",
            )
        if len(password) < 10 or password != confirm:
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="New password must be 10+ characters and match.",
            )
        studio.state.settings.password_hash = hash_password(password)
        studio.state.save_settings()
        studio.state.delete_all_sessions()
        studio.state.audit("password_changed", remote=client_host(request))
        return _login_response(studio, request, "/account?saved=1")

    @app.post("/account/options")
    async def account_options(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        auto_login_local: str = Form(""),
    ) -> Response:
        studio.state.settings.auto_login_local = auto_login_local == "on"
        studio.state.save_settings()
        return RedirectResponse("/account?saved=1", 303)

    @app.post("/account/signout-everywhere")
    async def signout_everywhere(
        request: Request, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        studio.state.delete_all_sessions()
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


# ---- helpers ------------------------------------------------------------------------------------
def _local_csrf(studio: Studio) -> str:
    token = studio.state.kv_get("local_csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        studio.state.kv_set("local_csrf", token)
    return str(token)


def _login_response(studio: Studio, request: Request, next_url: str) -> Response:
    sid, _csrf = studio.state.create_session(
        request.headers.get("user-agent", ""), request.client.host if request.client else ""
    )
    resp = RedirectResponse(next_url, 303)
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="strict",
        secure=studio.state.settings.remote_mode or request.url.scheme == "https",
        max_age=State.SESSION_MAX,
    )
    return resp


def _check_writable(studio: Studio, site_id: str) -> None:
    reason = studio.read_only_reason(site_id)
    if reason:
        raise HTTPException(423, f"site is read-only: {reason}")


def _try_load_site(root: Path, site_id: str) -> Any:
    try:
        return load_site(root, site_id=site_id)
    except ContentError:
        return None


def _releases(root: Path) -> list[dict[str, Any]]:
    p = root / ".constellation" / "releases.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("releases", []))[-10:][::-1]
    except (json.JSONDecodeError, AttributeError):
        return []


META_LIST_KEYS = ("tags", "categories")


def _meta_for_form(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    for k in META_LIST_KEYS:
        v = out.get(k)
        if isinstance(v, list):
            out[k] = ", ".join(str(x) for x in v)
    for k in ("date", "updated"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.replace(microsecond=0).isoformat()
    return out


def _meta_from_form(form: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for k, v in form.items():
        if v in (None, ""):
            continue
        if k in META_LIST_KEYS:
            meta[k] = [x.strip() for x in str(v).split(",") if x.strip()]
        elif k == "series_part" or k == "menu_order":
            try:
                meta[k] = int(v)
            except (TypeError, ValueError):
                continue
        elif k in ("seo", "platforms") and isinstance(v, dict):
            cleaned = {kk: vv for kk, vv in v.items() if vv not in (None, "", False)}
            if cleaned:
                meta[k] = cleaned
        else:
            meta[k] = v
    return meta


def _coerce(dotted: str, value: str) -> Any:
    key = dotted.rsplit(".", 1)[-1]
    if key in ("enabled", "image_link"):
        return value == "on"
    if key in (
        "pagination",
        "port",
        "long_cell_chars",
        "wide_table_cols",
        "width_map",
        "width_text",
    ):
        try:
            return int(value)
        except ValueError:
            return 0
    if key in ("hostnames", "feeds", "display"):
        return [x.strip() for x in value.split(",") if x.strip()]
    if key == "items":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value


def _coerce_type(type_name: str, value: str) -> Any:
    if type_name in ("int", "integer"):
        try:
            return int(value)
        except ValueError:
            return 0
    if type_name == "float":
        try:
            return float(value)
        except ValueError:
            return 0.0
    return value


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
