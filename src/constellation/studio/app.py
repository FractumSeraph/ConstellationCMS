"""The studio web application (FastAPI + Jinja2 + htmx)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

import tomlkit
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from constellation import __version__
from constellation.core import umami
from constellation.core.build.pipeline import media_type_for
from constellation.core.content import ContentError, load_site
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import RenderGemini
from constellation.core.render.gemtext import GemtextRenderer
from constellation.core.render.html import HtmlRenderer
from constellation.core.routes import RefResolver, url_for
from constellation.core.themes import BUILTIN_DIR, load_theme, theme_chain
from constellation.publish.export import is_git_target, parse_git_spec
from constellation.publish.github_api import GitHubAPI, GitHubError
from constellation.studio import content_ops, github, locks, looks, setup_code, updates
from constellation.studio.auth import (
    hash_password,
    host_is_loopback,
    is_loopback,
    safe_next,
    verify_password,
)
from constellation.studio.jobs import Jobs
from constellation.studio.preview import PreviewBuilder, gemtext_to_html, gophermap_to_html
from constellation.studio.state import State
from constellation.studio.workspace import (
    ServerEntry,
    Workspace,
    atomic_write,
    create_site,
    load_workspace,
    read_site_toml,
    save_workspace,
    set_path,
    write_site_toml,
)

HERE = Path(__file__).resolve().parent
SESSION_COOKIE = "constellation_session"
# A studio open to the internet with no authenticator needs at least this long a password.
REMOTE_PASSWORD_MIN = 12


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
    # Set by the desktop app: called when someone presses Quit in the studio.
    app.state.on_quit = None
    if studio.needs_setup():
        setup_code.ensure(state.dir)
    with contextlib.suppress(Exception):
        from constellation.studio import servers as _srv

        _srv.import_bootstrap(state, studio.workspace)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(
        {
            "version": __version__,
            # Changes whenever the studio's own scripts or styles change, so browsers (phones in
            # particular) fetch the new files instead of keeping old ones from their cache.
            "asset_v": _asset_version(HERE / "static"),
            "looks": looks.LOOKS,
            "modes": looks.MODES,
            "site_stars": looks.site_stars,
            "publish_sky": looks.publish_sky,
        }
    )
    templates.env.filters["tojson_attr"] = lambda v: json.dumps(v)
    templates.env.filters["when"] = lambda ts: (
        datetime.fromtimestamp(float(ts), UTC).strftime("%Y-%m-%d") if ts else ""
    )

    # ---- auth plumbing --------------------------------------------------------------------
    def client_host(request: Request) -> str:
        s = studio.state.settings
        if s.trust_proxy:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                # The proxy appends the address it saw; anything to the left is client-supplied.
                return fwd.split(",")[-1].strip()
        return request.client.host if request.client else ""

    def is_local(request: Request) -> bool:
        """A request from this machine, addressed to it, not forwarded by a proxy."""
        return (
            host_is_loopback(request.headers.get("host"))
            and is_loopback(request.client.host if request.client else None)
            and "x-forwarded-for" not in request.headers
        )

    def same_origin(request: Request) -> bool:
        """Browsers send Origin on every POST; it must name this studio."""
        origin = request.headers.get("origin")
        if not origin or origin == "null":
            return origin is None
        host = request.headers.get("host", "")
        if studio.state.settings.trust_proxy:
            host = request.headers.get("x-forwarded-host", host).split(",")[-1].strip()
        return urlsplit(origin).netloc.lower() == host.lower()

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
            and host_is_loopback(request.headers.get("host"))
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
            here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise HTTPException(
                status_code=307, headers={"Location": f"/login?next={quote(here, safe='')}"}
            )
        request.state.session = sess
        return sess

    async def require_csrf(
        request: Request, sess: dict[str, Any] = Depends(require_session)
    ) -> dict[str, Any]:
        """Mutating requests carry the session's CSRF token in a header (htmx, fetch) or a form field."""
        if not same_origin(request):
            raise HTTPException(403, "cross-origin request refused")
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
            response = cast(Response, await call_next(request))
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            if request.url.path.startswith("/sites/") and "/preview/" in request.url.path:
                # Built pages may contain raw HTML and scripts from content or themes. The
                # sandbox gives them an opaque origin, so they cannot use the studio session.
                response.headers.setdefault(
                    "Content-Security-Policy",
                    "sandbox allow-scripts allow-popups allow-forms; frame-ancestors 'self'",
                )
            else:
                response.headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src 'self'; frame-ancestors 'none'; form-action 'self'",
                )
            return response

    class LocalUntilRemote(BaseHTTPMiddleware):
        """Until remote mode is on, answer only requests addressed to this machine.

        Remote mode is the owner's deliberate choice to face the internet (with a long password,
        and an authenticator code if they set one up); before it, the studio stays on this machine.
        The one exception is first-run setup of a studio on a server, which other computers
        reach through the setup page, protected by the one-time setup code.
        """

        async def dispatch(self, request: Request, call_next: Any) -> Response:
            s = studio.state.settings
            if not s.remote_mode and not host_is_loopback(request.headers.get("host")):
                path = request.url.path
                if studio.needs_setup() and (path == "/setup" or path.startswith("/static/")):
                    return cast(Response, await call_next(request))
                if studio.needs_setup() and path == "/":
                    return RedirectResponse("/setup", 303)
                return Response(
                    "This studio is not open to other computers. Its owner can turn that on in "
                    "Account (remote mode, with an authenticator code). A new studio on a server "
                    "is set up with the setup link printed when the server was installed.\n",
                    status_code=403,
                    media_type="text/plain; charset=utf-8",
                )
            return cast(Response, await call_next(request))

    app.add_middleware(LocalUntilRemote)
    app.add_middleware(SecurityHeaders)

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        sess = getattr(request.state, "session", None)
        ctx.setdefault("csrf", sess["csrf"] if sess else "")
        ctx.setdefault("workspace", studio.workspace)
        ctx.setdefault("settings", studio.state.settings)
        ctx.setdefault("look", looks.effective_look(str(studio.state.settings.look)))
        ctx.setdefault("mode", looks.effective_mode(str(studio.state.settings.mode)))
        ctx.setdefault("update", updates.available(studio.state) if ctx["csrf"] else None)
        ctx.setdefault("desktop", app.state.on_quit is not None)
        ctx.setdefault("update_check", updates.enabled(studio.state))
        ctx.setdefault("gh_login", github.login(studio.state) if ctx["csrf"] else "")
        return cast(HTMLResponse, templates.TemplateResponse(request, name, ctx))

    # ---- setup, login, logout -------------------------------------------------------------
    @app.get("/setup", response_class=HTMLResponse)
    async def setup_form(request: Request, code: str = "") -> Response:
        if not studio.needs_setup():
            return RedirectResponse("/", 303)
        if not is_local(request):
            return _remote_setup_page(request, "", code)
        return render(request, "setup.html", error="", chosen=looks.DEFAULT_LOOK)

    def _remote_setup_page(
        request: Request, error: str, code: str, secret: str = "", status: int = 200
    ) -> Response:
        from constellation.studio import totp as _totp

        secret = secret or _totp.new_secret()
        uri = _totp.provisioning_uri(secret, request.headers.get("host", "studio"))
        resp = render(
            request,
            "setup_remote.html",
            error=error,
            setup_code=code,
            secret=secret,
            qr=_totp.qr_svg(uri),
            chosen=looks.DEFAULT_LOOK,
        )
        resp.status_code = status
        return resp

    @app.post("/setup")
    async def setup_submit(
        request: Request,
        password: str = Form(...),
        confirm: str = Form(...),
        look: str = Form(looks.DEFAULT_LOOK),
        mode: str = Form(looks.DEFAULT_MODE),
        setup_code_value: str = Form("", alias="setup_code"),
        secret: str = Form(""),
        otp: str = Form(""),
    ) -> Response:
        if not studio.needs_setup():
            return RedirectResponse("/", 303)
        if not same_origin(request):
            raise HTTPException(403, "cross-origin request refused")
        if not is_local(request):
            return _remote_setup_submit(
                request, password, confirm, look, mode, setup_code_value, secret, otp
            )
        chosen = looks.effective_look(look)
        if len(password) < 10:
            return render(request, "setup.html", error="Use at least 10 characters.", chosen=chosen)
        if password != confirm:
            return render(request, "setup.html", error="The two passwords differ.", chosen=chosen)
        studio.state.settings.password_hash = hash_password(password)
        studio.state.settings.look = chosen
        studio.state.settings.mode = looks.effective_mode(mode)
        studio.state.save_settings()
        setup_code.clear(studio.state.dir)
        studio.state.audit("setup", remote=client_host(request))
        return _login_response(studio, request, _after_setup(studio))

    def _remote_setup_submit(
        request: Request,
        password: str,
        confirm: str,
        look: str,
        mode: str,
        code: str,
        secret: str,
        otp: str,
    ) -> Response:
        from constellation.studio import totp as _totp

        host = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (
            request.client.host if request.client else ""
        )
        if studio.state.login_locked(host):
            return _remote_setup_page(
                request, "Too many wrong setup codes; try again later.", code, secret, 429
            )
        if not setup_code.matches(studio.state.dir, code):
            studio.state.login_failed(host)
            studio.state.audit("setup_bad_code", remote=host)
            return _remote_setup_page(
                request,
                "That setup code is not right. Use the link printed when the server was installed.",
                code,
                secret,
                403,
            )
        otp = otp.replace(" ", "")
        if len(password) < (10 if otp else REMOTE_PASSWORD_MIN):
            return _remote_setup_page(
                request,
                f"Use at least {10 if otp else REMOTE_PASSWORD_MIN} characters for the password"
                + ("." if otp else " (or add an authenticator app below)."),
                code,
                secret,
            )
        if password != confirm:
            return _remote_setup_page(request, "The two passwords differ.", code, secret)
        if otp and (not secret or not _totp.verify(secret, otp)):
            return _remote_setup_page(
                request,
                "That authenticator code didn't match. Scan the QR code again and type the current code, or leave it empty to skip the authenticator.",
                code,
                secret,
            )
        s = studio.state.settings
        s.password_hash = hash_password(password)
        s.totp_secret = secret if otp else ""
        s.remote_mode = True
        # Reached through a proxy (Caddy, nginx or the server program): trust its client address.
        s.trust_proxy = "x-forwarded-for" in request.headers
        s.look = looks.effective_look(look)
        s.mode = looks.effective_mode(mode)
        studio.state.save_settings()
        counter = _totp.match(secret, otp) if otp else None
        if counter is not None:
            studio.state.kv_set("totp_last_counter", counter)
        setup_code.clear(studio.state.dir)
        studio.state.audit("setup_remote", remote=host)
        return _login_response(studio, request, _after_setup(studio))

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/") -> Response:
        if studio.needs_setup():
            return RedirectResponse("/setup", 303)
        if session_of(request):
            return RedirectResponse(safe_next(next), 303)
        return render(
            request, "login.html", error="", next=next, totp=bool(studio.state.settings.totp_secret)
        )

    @app.post("/login")
    async def login_submit(
        request: Request, password: str = Form(...), next: str = Form("/"), code: str = Form("")
    ) -> Response:
        if not same_origin(request):
            raise HTTPException(403, "cross-origin request refused")
        host = client_host(request)
        locked = studio.state.login_locked(host)
        if locked:
            studio.state.audit("login_locked", remote=host)
            return render(
                request,
                "login.html",
                error=f"Too many failures; try again in {int(locked // 60) + 1} minutes.",
                next=next,
                totp=bool(studio.state.settings.totp_secret),
            )
        if not verify_password(studio.state.settings.password_hash, password):
            studio.state.login_failed(host)
            studio.state.audit("login_failed", remote=host)
            return render(
                request,
                "login.html",
                error="Wrong password.",
                next=next,
                totp=bool(studio.state.settings.totp_secret),
            )
        if studio.state.settings.totp_secret:
            from constellation.studio import totp as _totp

            secret = studio.state.settings.totp_secret
            last = studio.state.kv_get("totp_last_counter")
            counter = _totp.match(secret, code)
            if counter is not None and last is not None and counter <= int(last):
                counter = None  # already used: refuse replays
            if counter is not None:
                studio.state.kv_set("totp_last_counter", counter)
            if counter is None:
                studio.state.login_failed(host)
                studio.state.audit("login_failed_totp", remote=host)
                return render(
                    request,
                    "login.html",
                    error="Wrong or missing authenticator code.",
                    next=next,
                    totp=True,
                )
        studio.state.login_succeeded(host)
        studio.state.audit("login", remote=host)
        return _login_response(studio, request, safe_next(next))

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

    # ---- first run: make a site, choose where it goes, write the first post ---------------
    def _theme_cards() -> list[dict[str, str]]:
        names = sorted(
            {p.name for p in BUILTIN_DIR.iterdir() if (p / "theme.toml").is_file()}
            | {
                p.name
                for p in (studio.workspace_root / "themes").glob("*")
                if (p / "theme.toml").is_file()
            }
        )
        order = {name: i for i, name in enumerate(THEME_BLURBS)}
        cards = []
        for name in sorted(names, key=lambda n: (order.get(n, len(order)), n)):
            label, blurb = THEME_BLURBS.get(name, ("", ""))
            if not blurb:
                with contextlib.suppress(Exception):
                    blurb = load_theme(name, search=[studio.workspace_root / "themes"]).description
            picture = HERE / "static" / "themes" / f"{name}.webp"
            cards.append(
                {
                    "id": name,
                    "name": label or name.replace("-", " ").title(),
                    "blurb": blurb,
                    "picture": f"/static/themes/{name}.webp" if picture.is_file() else "",
                }
            )
        return cards

    @app.get("/welcome", response_class=HTMLResponse)
    async def welcome(
        request: Request, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        form = {"name": "", "author": "", "theme": "base", "web_url": ""}
        return render(request, "welcome.html", error="", themes=_theme_cards(), form=form)

    @app.post("/welcome")
    async def welcome_submit(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        name: str = Form(""),
        author: str = Form(""),
        theme: str = Form("base"),
        web_url: str = Form(""),
        timezone: str = Form("UTC"),
    ) -> Response:
        form = {"name": name, "author": author, "theme": theme, "web_url": web_url}
        cards = _theme_cards()
        if theme not in {c["id"] for c in cards}:
            theme = "base"
        error = ""
        url = web_url.strip()
        if url and "://" not in url:
            url = "https://" + url
        if url and not re.fullmatch(r"https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(:\d+)?/?", url):
            error = "That web address doesn't look right. Use just the domain, like example.com, or leave it empty."
        if not name.strip():
            error = "Give your site a name."
        if not error:
            try:
                entry = create_site(
                    studio.workspace,
                    name.strip(),
                    site_id=_free_site_id(studio, name.strip()),
                    web_url=url.rstrip("/").lower(),
                    timezone=_valid_timezone(timezone),
                    author=author.strip(),
                    theme=theme,
                )
            except ValueError as e:
                error = str(e)
            else:
                studio.state.audit("site_created", entry.id, client_host(request))
                return RedirectResponse(f"/welcome/{entry.id}/where", 303)
        resp = render(request, "welcome.html", error=error, themes=cards, form=form)
        resp.status_code = 400
        return resp

    @app.get("/welcome/{site_id}/where", response_class=HTMLResponse)
    async def welcome_where(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        ws = studio.workspace
        entry = ws.site(site_id)
        if entry is None:
            raise HTTPException(404, f"no site {site_id!r}")
        return render(
            request,
            "welcome_where.html",
            wizard_site=site_id,
            site_name=entry.name,
            servers=ws.servers,
        )

    @app.post("/welcome/{site_id}/where")
    async def welcome_where_submit(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        place: str = Form("local"),
        server_id: str = Form(""),
    ) -> Response:
        ws = studio.workspace
        entry = ws.site(site_id)
        if entry is None:
            raise HTTPException(404, f"no site {site_id!r}")
        if place == "github":
            return RedirectResponse(f"/sites/{site_id}/github?wizard=1", 303)
        if place == "server" and ws.server(server_id) is not None:
            entry.server = server_id
            save_workspace(ws)
            studio.state.audit("site_assigned", f"{site_id}->{server_id}", client_host(request))
        return RedirectResponse(f"/welcome/{site_id}/write", 303)

    @app.get("/welcome/{site_id}/write", response_class=HTMLResponse)
    async def welcome_write(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        studio.site_root(site_id)
        return render(request, "welcome_write.html", wizard_site=site_id)

    @app.post("/welcome/{site_id}/write")
    async def welcome_write_submit(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        title: str = Form("Hello, world"),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        rel = content_ops.create_item(root, "post", title.strip() or "Hello, world")
        studio.state.audit("created", rel, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/edit?rel={quote(rel)}&mode=visual", 303)

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
        if kind not in ("post", "page"):
            raise HTTPException(400, "kind must be post or page")
        rel = content_ops.create_item(root, kind, title.strip() or "Untitled")
        studio.state.audit("created", rel, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/edit?rel={quote(rel)}", 303)

    @app.get("/sites/{site_id}/edit", response_class=HTMLResponse)
    async def editor(
        request: Request, site_id: str, rel: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        studio.touch_lock(site_id)
        try:
            item = content_ops.load_item(root, rel, state=studio.state, site_id=site_id)
        except FileNotFoundError as e:
            raise HTTPException(404, "no such post or page") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        site = _try_load_site(root, site_id)
        authors = [a.id for a in site.config.authors] if site else []
        tz = _site_timezone(root)
        return render(
            request,
            "editor.html",
            site_id=site_id,
            item=item,
            rel=rel,
            authors=authors,
            date_input=_date_for_input(item.meta.get("date"), tz),
            timezone=str(tz),
            meta_json=json.dumps(_meta_for_form(item.meta), default=str),
            read_only=studio.read_only_reason(site_id),
            gemini_enabled=bool(site and site.config.platform("gemini").enabled),
            gopher_enabled=bool(site and site.config.platform("gopher").enabled),
        )

    @app.get("/sites/{site_id}/raw")
    async def raw_body(
        request: Request, site_id: str, rel: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        root = studio.site_root(site_id)
        try:
            item = content_ops.load_item(root, rel)
        except FileNotFoundError as e:
            raise HTTPException(404, "no such post or page") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return Response(item.body, media_type="text/plain; charset=utf-8")

    @app.post("/sites/{site_id}/save")
    async def save(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        payload = await request.json()
        rel = str(payload["rel"])
        try:
            original = content_ops.load_item(root, rel).meta
        except (FileNotFoundError, ValueError):
            original = {}
        except Exception:  # noqa: BLE001 - unreadable front matter: start from the form
            original = {}
        meta = _merge_meta(original, payload.get("meta", {}), _site_timezone(root))
        problem = _validate_meta(rel, meta)
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
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

    @app.post("/sites/{site_id}/visual/blocks")
    async def visual_blocks(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        """Markdown → blocks for the editor's visual mode (editable HTML or locked source)."""
        from constellation.core.markdown.document import Ref
        from constellation.studio import visual

        root = studio.site_root(site_id)
        payload = await request.json()
        body = str(payload.get("body", ""))
        slug = str(payload.get("slug") or "preview")
        rel_path = str(payload.get("rel", "")).replace("\\", "/")
        item_kind = "page" if "content/pages/" in rel_path else "post"
        site = _try_load_site(root, site_id)
        from constellation.core.models import SiteConfig, SiteInfo

        cfg = site.config if site else SiteConfig(site=SiteInfo(title="Preview"))

        def media_url(ref: Ref) -> str:
            if ref.kind == "external":
                return ref.target
            path = url_for(ref, "web", cfg, item_slug=slug, item_kind=item_kind)
            return f"/sites/{quote(site_id)}/preview/web{path}" if path.startswith("/") else path

        return JSONResponse(visual.to_blocks(body, media_url))

    @app.post("/sites/{site_id}/visual/markdown")
    async def visual_markdown(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        """Edited visual blocks → Markdown."""
        from constellation.studio import visual

        studio.site_root(site_id)
        payload = await request.json()
        items = payload.get("items", [])
        if not isinstance(items, list) or len(items) > 5000:
            raise HTTPException(400, "bad items")
        clean = [i for i in items if isinstance(i, dict)]
        lead, tail = str(payload.get("lead", "")), str(payload.get("tail", "\n"))
        return JSONResponse({"body": visual.to_markdown(clean, lead=lead, tail=tail)})

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
        rel_path = str(payload.get("rel", "")).replace("\\", "/")
        item_kind = "page" if "content/pages/" in rel_path else "post"
        site = _try_load_site(root, site_id)
        cfg = site.config if site else None
        resolver = RefResolver(
            cfg,
            post_slugs={p.slug for p in site.posts} if site else set(),
            page_slugs={p.slug for p in site.pages} if site else set(),
        )
        if site is not None and site.config.plugins.enabled:
            from constellation.core import plugins

            body = plugins.expand(body, plugins.load(site), site)
        doc = parse_markdown(body, resolve=lambda h, i: resolver.resolve(h, is_image=i))
        media_warnings = _missing_media_warnings(root, str(payload.get("rel", "")), doc, platform)
        from constellation.core.models import SiteConfig, SiteInfo

        cfg = cfg or SiteConfig(site=SiteInfo(title="Preview"))
        if platform == "gopher":
            from constellation.core.render.gopher import GopherRenderer, GopherTarget

            pcfg = cfg.platform("gopher")
            target = GopherTarget(
                pcfg.hostnames[0] if pcfg.hostnames else "localhost", pcfg.port or 70
            )
            renderer = GopherRenderer(
                cfg.render.gopher,
                lambda r: url_for(r, "gopher", cfg, item_slug=slug, item_kind=item_kind),
                target,
            )
            res = renderer.render_text(doc)
            map_res = renderer.render(doc)
            return JSONResponse(
                {
                    "html": "",
                    "text": res.text + "\n\n--- gophermap ---\n" + map_res.text,
                    "media_base": "",
                    "warnings": media_warnings + [w.as_dict() for w in map_res.warnings],
                }
            )
        if platform == "gemini":
            res = GemtextRenderer(
                cfg.render.gemini if cfg else RenderGemini(),
                lambda r: url_for(r, "gemini", cfg, item_slug=slug, item_kind=item_kind),
            ).render(doc)
            title = str(meta.get("title", ""))
            html_out = gemtext_to_html((f"# {title}\n\n" if title else "") + res.text)
            text = res.text
        else:
            res = HtmlRenderer(
                lambda r: url_for(r, "web", cfg, item_slug=slug, item_kind=item_kind)
            ).render(doc)
            html_out = res.text
            text = ""
        media_base = f"/sites/{site_id}/preview/web/media/{item_kind}s/{slug}/"
        return JSONResponse(
            {
                "html": html_out,
                "text": text,
                "media_base": media_base,
                "warnings": media_warnings + [w.as_dict() for w in res.warnings],
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
        except (ValueError, OSError) as e:
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
        try:
            content_ops.trash_item(root, rel, state=studio.state, site_id=site_id)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        studio.state.audit("trashed", rel, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/{'page' if kind == 'page' else 'post'}s", 303)

    @app.post("/sites/{site_id}/duplicate")
    async def duplicate(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        rel: str = Form(...),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        try:
            new_rel = content_ops.duplicate_item(root, rel)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse(f"/sites/{site_id}/edit?rel={quote(new_rel)}", 303)

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
        index = {"web": "index.html", "gemini": "index.gmi", "gopher": "gophermap"}[platform]
        key = path.lstrip("/")
        candidates = [key] if key and not key.endswith("/") else []
        candidates += [f"{key.rstrip('/')}/{index}".lstrip("/")]
        if platform == "gemini" and key:
            candidates.append(key.rstrip("/") + ".gmi")
        key = next((c for c in candidates if c in files), "")
        if not key:
            raise HTTPException(404)
        f = files[key]
        data = f.bytes()
        prefix = f"/sites/{site_id}/preview/{platform}"
        if platform == "gemini" and f.media_type.startswith("text/gemini"):
            return render(
                request,
                "gemini_preview.html",
                site_id=site_id,
                body=gemtext_to_html(data.decode("utf-8"), prefix=prefix),
                path=key,
            )
        if platform == "gopher" and key.endswith("gophermap"):
            hosts = {h.lower() for h in build_hosts(root, "gopher")}
            return render(
                request,
                "gemini_preview.html",
                site_id=site_id,
                body=gophermap_to_html(data.decode("utf-8"), prefix, hosts),
                path=key,
                label="Gopher",
            )
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
            text = re.sub(r"(url\(['\"]?)/(?!/)", lambda m: m.group(1) + prefix + "/", text)
            return HTMLResponse(text)
        if f.media_type.startswith("text/css"):
            text = re.sub(
                r"(url\(['\"]?)/(?!/)", lambda m: m.group(1) + prefix + "/", data.decode("utf-8")
            )
            return Response(text, media_type=f.media_type)
        media = f.media_type or media_type_for(key)
        headers = {}
        if media.startswith("font/") or key.endswith((".woff2", ".woff", ".ttf", ".otf")):
            # The editor's preview runs in a sandboxed frame (origin "null"), and browsers only
            # load fonts across origins when the response allows it.
            headers["Access-Control-Allow-Origin"] = "*"
        return Response(data, media_type=media, headers=headers)

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
    @app.get("/sites/{site_id}/domain", response_class=HTMLResponse)
    async def domain_page(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        studio.site_root(site_id)
        return render(request, "domain.html", site_id=site_id)

    @app.get("/sites/{site_id}/domain/check", response_class=HTMLResponse)
    async def domain_check(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        from constellation.core.content import load_config
        from constellation.studio import domains

        root = studio.site_root(site_id)
        entry = studio.workspace.site(site_id)
        server = studio.workspace.server(entry.server) if entry and entry.server else None
        cfg = load_config(root)
        report = await asyncio.to_thread(
            domains.expected_records, cfg, server.admin_url if server else ""
        )
        https = ""
        if report.ok:
            first = next((r.name for r in report.records if "web" in r.why), "")
            works = bool(first) and await asyncio.to_thread(domains.https_works, first)
            https = first if works else ""
        return render(
            request,
            "partials/domain_check.html",
            site_id=site_id,
            report=report,
            https=https,
            checked=datetime.now(UTC).strftime("%H:%M:%S UTC"),
        )

    @app.get("/sites/{site_id}/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        saved: str = "",
        moved: str = "",
    ) -> Response:
        root = studio.site_root(site_id)
        doc = read_site_toml(root)
        return render(
            request,
            "settings.html",
            **_settings_extras(root),
            site_id=site_id,
            doc=doc,
            toml_text=tomlkit.dumps(doc),
            saved=saved,
            umami_moved=moved,
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
                _validate_site_doc(new_doc)
            except Exception as e:  # noqa: BLE001
                return render(
                    request,
                    "settings.html",
                    **_settings_extras(root),
                    site_id=site_id,
                    doc=doc,
                    toml_text=str(form["toml_text"]),
                    saved="",
                    read_only="",
                    error=f"Not saved: {e}",
                )
            write_site_toml(root, new_doc)
        else:
            try:
                for key, value in form.multi_items():
                    if key in ("csrf", "f.features.share") or not key.startswith("f."):
                        continue
                    dotted = key[2:]
                    set_path(doc, dotted, _coerce(dotted, str(value)))
                if "share_present" in form:
                    shares = [str(v) for v in form.getlist("f.features.share") if str(v)]
                    set_path(doc, "features.share", shares)
                _validate_site_doc(doc)
            except ValueError as e:
                return render(
                    request,
                    "settings.html",
                    **_settings_extras(root),
                    site_id=site_id,
                    doc=read_site_toml(root),
                    toml_text=tomlkit.dumps(read_site_toml(root)),
                    saved="",
                    read_only="",
                    error=f"Not saved: {e}",
                )
            write_site_toml(root, doc)
        studio.state.audit("settings", site_id, client_host(request))
        return RedirectResponse(f"/sites/{site_id}/settings?saved=1", 303)

    @app.post("/sites/{site_id}/plugins")
    async def plugins_save(
        request: Request, site_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        from constellation.core import plugins

        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        form = await request.form()
        found = {p.name: p for p in plugins.available(root) if not p.error}
        enabled = [str(v) for v in form.getlist("enabled") if str(v) in found]
        doc = read_site_toml(root)
        table = doc.get("plugins")
        if table is None:
            table = tomlkit.table()
            doc["plugins"] = table
        table["enabled"] = enabled
        settings = table.get("settings")
        if settings is None:
            settings = tomlkit.table()
            table["settings"] = settings
        for name, avail in found.items():
            values: dict[str, Any] = {}
            for st in avail.settings:
                raw = form.getlist(f"s.{name}.{st.key}")
                if not raw:
                    continue
                value = str(raw[-1])
                if st.type == "bool":
                    values[st.key] = value == "on"
                elif st.type == "int":
                    try:
                        values[st.key] = int(value)
                    except ValueError:
                        raise HTTPException(
                            400, f"{name}: {st.label or st.key} must be a whole number"
                        ) from None
                elif st.type == "choice" and st.choices and value not in st.choices:
                    raise HTTPException(400, f"{name}: {value!r} is not one of the choices")
                else:
                    values[st.key] = value
            if values:
                t = tomlkit.table()
                for k, v in values.items():
                    t[k] = v
                settings[name] = t
        _validate_site_doc(doc)
        write_site_toml(root, doc)
        studio.state.audit("plugins", f"{site_id}:{','.join(enabled)}", client_host(request))
        return RedirectResponse(f"/sites/{site_id}/settings?saved=1#plugins", 303)

    @app.post("/sites/{site_id}/umami")
    async def umami_save(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        action: str = Form("save"),
        code: str = Form(""),
        forward: str = Form(""),
    ) -> Response:
        """Read a pasted Umami tracking code and store what the site needs from it."""
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        doc = read_site_toml(root)
        if action == "remove":
            if "analytics" in doc and "umami" in doc["analytics"]:
                del doc["analytics"]["umami"]
            write_site_toml(root, doc)
            studio.state.audit("umami_removed", site_id, client_host(request))
            return RedirectResponse(f"/sites/{site_id}/settings?saved=1#umami", 303)
        if action == "forward":
            set_path(doc, "analytics.umami.forward_other_platforms", forward == "on")
            write_site_toml(root, doc)
            return RedirectResponse(f"/sites/{site_id}/settings?saved=1#umami", 303)
        try:
            parsed = umami.parse_tracking_code(code)
        except umami.UmamiCodeError as e:
            return render(
                request,
                "settings.html",
                **_settings_extras(root),
                site_id=site_id,
                doc=doc,
                toml_text=tomlkit.dumps(doc),
                saved="",
                read_only="",
                error="",
                umami_error=str(e),
                umami_code=code,
            )
        table = tomlkit.table()
        table["script_url"] = parsed.script_url
        table["website_id"] = parsed.website_id
        if parsed.host_url:
            table["host_url"] = parsed.host_url
        table["forward_other_platforms"] = True
        if "analytics" not in doc:
            doc["analytics"] = tomlkit.table()
        doc["analytics"]["umami"] = table
        moved = False
        head = str(doc.get("custom_html", {}).get("head", ""))
        if head:
            new_head, moved = umami.strip_matching_snippet(head, parsed.website_id)
            if moved:
                set_path(doc, "custom_html.head", new_head)
        _validate_site_doc(doc)
        write_site_toml(root, doc)
        studio.state.audit("umami_set", site_id, client_host(request))
        return RedirectResponse(
            f"/sites/{site_id}/settings?saved=1{'&moved=1' if moved else ''}#umami", 303
        )

    # ---- theme ------------------------------------------------------------------------------
    @app.get("/sites/{site_id}/theme", response_class=HTMLResponse)
    async def theme_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        error: str = "",
        installed: str = "",
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
            error=error,
            installed=installed,
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
        current = str(doc.get("site", {}).get("theme", "base"))
        chosen = str(form.get("theme", current))
        theme = load_theme(chosen, search=[studio.workspace_root / "themes"])
        set_path(doc, "site.theme", chosen)
        if chosen != current:
            # The form showed the old theme's settings; switching only changes the theme, and
            # the new theme's settings start from its own defaults.
            write_site_toml(root, doc)
            return RedirectResponse(f"/sites/{site_id}/theme", 303)
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
        errors = []
        seen: set[tuple[str, str]] = set()
        for pb in build.platforms.values() if build else []:
            for w in pb.warnings:
                if w.severity == "error" and (w.code, w.message) not in seen:
                    seen.add((w.code, w.message))
                    errors.append(w)
        from constellation.studio import servers as srv

        ws = studio.workspace
        entry = ws.site(site_id)
        server = ws.server(entry.server) if entry and entry.server else None
        remote = srv.releases(studio.state, site_id, server) if server else None
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
            has_github=any(str(e.get("target", "")).startswith("github:") for e in exports),
            server=server,
            remote=remote,
            connected=bool(
                server
                and (
                    srv.has_token(studio.state, server.id, site_id)
                    or srv.admin_key(studio.state, server.id)
                )
            ),
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
        with_publish: str = Form(""),
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
            problem = _export_problem(
                name.strip(),
                platform,
                target.strip(),
                layout,
                [str(e.get("name")) for e in exports],
            )
            if problem:
                raise HTTPException(400, problem)
            t = tomlkit.table()
            t["name"], t["platform"], t["target"], t["layout"] = (
                name.strip(),
                platform,
                target.strip(),
                layout,
            )
            if with_publish:
                t["with_publish"] = True
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
        if mode == "apply":
            _check_writable(studio, site_id)
        doc = read_site_toml(root)
        spec = next((dict(e) for e in doc.get("exports", []) if e.get("name") == name), None)
        if spec is None:
            raise HTTPException(404, "no such export")
        from constellation.studio.publish_ops import run_export

        try:
            job = await studio.jobs.start(
                "export",
                site_id,
                lambda job: run_export(
                    job,
                    root,
                    site_id,
                    spec,
                    apply=mode == "apply",
                    github_token=github.token(studio.state),
                    client=app.state.github_client,
                ),
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

    # ---- servers (registry, tokens, publish to our server) -----------------------------------
    @app.get("/servers", response_class=HTMLResponse)
    async def servers_page(
        request: Request,
        sess: dict[str, Any] = Depends(require_session),
        error: str = "",
        ok: str = "",
    ) -> Response:
        from constellation.studio import servers as srv

        ws = studio.workspace
        connected = {
            (s.id, site.id): srv.has_token(studio.state, s.id, site.id)
            for s in ws.servers
            for site in ws.sites
        }
        studio_links = {
            s.id: str(studio.state.kv_get(f"studio_setup_link:{s.id}", "") or "")
            for s in ws.servers
        }
        linked = {s.id: bool(srv.admin_key(studio.state, s.id)) for s in ws.servers}
        return render(
            request,
            "servers.html",
            servers=ws.servers,
            sites=ws.sites,
            connected=connected,
            studio_links=studio_links,
            linked=linked,
            error=error,
            ok=ok,
        )

    @app.post("/servers/{server_id}/studio-link/hide")
    async def servers_studio_link_hide(
        request: Request, server_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        studio.state.kv_set(f"studio_setup_link:{server_id}", "")
        return RedirectResponse("/servers", 303)

    @app.post("/servers")
    async def servers_add(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        id: str = Form(""),
        name: str = Form(""),
        admin_url: str = Form(...),
        delete: str = Form(""),
        secret: str = Form(""),
    ) -> Response:
        from urllib.parse import quote as _q

        from slugify import slugify

        from constellation.studio import servers as srv

        ws = studio.workspace
        if delete:
            ws.servers = [s for s in ws.servers if s.id != delete]
            for site in ws.sites:
                if site.server == delete:
                    site.server = ""
            save_workspace(ws)
            srv.disconnect_computer(studio.state, delete)
            studio.state.audit("servers_changed", delete, client_host(request))
            return RedirectResponse("/servers?ok=1", 303)
        url = admin_url.strip()
        if url and "://" not in url:
            url = "https://" + url
        if not url.startswith("https://"):
            return RedirectResponse(
                "/servers?error=" + _q("The address must start with https://"), 303
            )
        url = _admin_url(url)
        existing = next((s for s in ws.servers if s.admin_url == url), None)
        if existing is not None:
            server = existing
        else:
            base = (
                slugify(id.strip() or name.strip() or urlsplit(url).hostname or "server")[:40]
                or "server"
            )
            sid, n = base, 2
            while ws.server(sid) is not None:
                sid, n = f"{base}-{n}", n + 1
            server = ServerEntry(sid, name.strip() or sid, url)
            ws.servers.append(server)
            save_workspace(ws)
        studio.state.audit("servers_changed", server.id, client_host(request))
        if secret.strip():
            try:
                srv.connect_computer(
                    studio.state, server, secret, f"studio on {locks.machine_label()}"
                )
            except Exception as e:  # noqa: BLE001
                return RedirectResponse(
                    "/servers?error=" + _q(f"Added {server.name}, but connecting failed: {e}"), 303
                )
            studio.state.audit("computer_connected", server.id, client_host(request))
        return RedirectResponse(f"/servers?ok=1#server-{server.id}", 303)

    # ---- guided server setup ------------------------------------------------------------
    # The wizard's details between its pages, in memory only: the root password is never
    # written anywhere, and a draft is forgotten after half an hour or once the setup starts.
    setup_drafts: dict[str, dict[str, Any]] = {}

    def _draft(token: str) -> dict[str, Any] | None:
        now = time.monotonic()
        for k in [k for k, d in setup_drafts.items() if d["expires"] < now]:
            setup_drafts.pop(k, None)
        return setup_drafts.get(token)

    @app.get("/servers/setup", response_class=HTMLResponse)
    async def server_setup_page(
        request: Request, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        return render(request, "setup_server.html", step=1, error="", host="", user="", port=22)

    @app.post("/servers/setup/check", response_class=HTMLResponse)
    async def server_setup_check(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        host: str = Form(...),
        password: str = Form(""),
        user: str = Form("root"),
        port: int = Form(22),
    ) -> Response:
        from constellation.studio import provision

        host = host.strip().removeprefix("ssh://").split("/")[0]
        user = user.strip() or "root"
        if not re.fullmatch(r"[A-Za-z0-9.:\-\[\]]{1,253}", host) or not re.fullmatch(
            r"[a-z_][a-z0-9_-]{0,31}", user
        ):
            return render(
                request,
                "setup_server.html",
                step=1,
                error="That doesn't look like an IP address or server name.",
                host=host,
                user=user,
                port=port,
            )

        def look() -> provision.Check:
            remote = provision.SSHRemote(
                host,
                user=user,
                port=port,
                password=password,
                known_hosts=studio.state.dir / "known_hosts",
            )
            try:
                return provision.inspect(remote)
            finally:
                remote.close()

        try:
            check = await asyncio.to_thread(look)
        except provision.ProvisionError as e:
            return render(
                request, "setup_server.html", step=1, error=str(e), host=host, user=user, port=port
            )
        # The address the person typed is the one the internet sees; the server's own idea of
        # its address can be a private one behind the provider's network.
        ip = host if provision.auto_host(host) else (check.public_ip or host)
        plan = provision.make_plan(check, provision.auto_host(ip) or host)
        token = secrets.token_urlsafe(16)
        setup_drafts[token] = {
            "host": host,
            "user": user,
            "port": port,
            "password": password,
            "check": check,
            "ip": ip,
            "expires": time.monotonic() + 1800,
        }
        studio.state.audit("server_setup_checked", host, client_host(request))
        return render(
            request,
            "setup_server.html",
            step=2,
            error="",
            check=check,
            plan=plan,
            draft=token,
            host=host,
            server_name=f"Server {ip}",
            studio_default=provision.auto_host(ip, "studio") or f"studio.{plan.admin_host}",
        )

    @app.post("/servers/setup/run", response_class=HTMLResponse)
    async def server_setup_run(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        draft: str = Form(...),
        name: str = Form(""),
        admin_host: str = Form(...),
        studio_on: str = Form("", alias="studio"),
        gopher: str = Form(""),
    ) -> Response:
        from constellation.studio import provision
        from constellation.studio.server_setup import run_setup

        d = _draft(draft)
        if d is None:
            return render(
                request,
                "setup_server.html",
                step=1,
                error="That setup page expired; please start again.",
                host="",
                user="",
                port=22,
            )
        check = d["check"]
        studio_host = (
            (provision.auto_host(d["ip"], "studio") or f"studio.{admin_host}") if studio_on else ""
        )
        plan = provision.make_plan(
            check, admin_host.strip().lower(), studio_host=studio_host, gopher=bool(gopher)
        )
        if not plan.ok:
            return render(
                request,
                "setup_server.html",
                step=2,
                error="",
                check=check,
                plan=plan,
                draft=draft,
                host=d["host"],
                server_name=name,
                studio_default=studio_host or provision.auto_host(d["ip"], "studio"),
            )
        setup_drafts.pop(draft, None)  # the password lives only in the job from here
        label = f"studio on {locks.machine_label()}"
        try:
            job = await studio.jobs.start(
                "server setup",
                "_servers",
                lambda job: run_setup(
                    job,
                    studio.state,
                    studio.workspace_root,
                    host=d["host"],
                    port=d["port"],
                    user=d["user"],
                    password=d["password"],
                    name=name.strip(),
                    plan=plan,
                    check=check,
                    label=label,
                ),
            )
        except RuntimeError as e:
            return render(
                request,
                "setup_server.html",
                step=1,
                error=str(e),
                host=d["host"],
                user=d["user"],
                port=d["port"],
            )
        studio.state.audit("server_setup_started", d["host"], client_host(request))
        return render(request, "setup_server.html", step=3, error="", job=job, site_id="")

    @app.post("/servers/{server_id}/connect-computer")
    async def servers_connect_computer(
        request: Request,
        server_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        secret: str = Form(...),
    ) -> Response:
        from urllib.parse import quote as _q

        from constellation.studio import servers as srv

        server = studio.workspace.server(server_id)
        if server is None:
            raise HTTPException(404)
        try:
            srv.connect_computer(studio.state, server, secret, f"studio on {locks.machine_label()}")
        except Exception as e:  # noqa: BLE001
            return RedirectResponse("/servers?error=" + _q(f"Could not connect: {e}"), 303)
        studio.state.audit("computer_connected", server_id, client_host(request))
        return RedirectResponse(f"/servers?ok=1#server-{server_id}", 303)

    @app.post("/servers/{server_id}/disconnect-computer")
    async def servers_disconnect_computer(
        request: Request, server_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        from constellation.studio import servers as srv

        srv.disconnect_computer(studio.state, server_id)
        studio.state.audit("computer_disconnected", server_id, client_host(request))
        return RedirectResponse("/servers?ok=1", 303)

    @app.post("/servers/{server_id}/pairing-code", response_class=HTMLResponse)
    async def servers_pairing_code(
        request: Request, server_id: str, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        from constellation.studio import servers as srv

        server = studio.workspace.server(server_id)
        if server is None:
            raise HTTPException(404)
        try:
            made = await asyncio.to_thread(srv.pairing_code, studio.state, server)
        except Exception as e:  # noqa: BLE001
            return render(request, "partials/pairing.html", error=str(e), server=server)
        studio.state.audit("pairing_code", server_id, client_host(request))
        return render(
            request,
            "partials/pairing.html",
            error="",
            server=server,
            code=made.get("code", ""),
            minutes=int(made.get("expires_in", 600)) // 60,
        )

    @app.get("/servers/{server_id}/computers", response_class=HTMLResponse)
    async def servers_computers(
        request: Request, server_id: str, sess: dict[str, Any] = Depends(require_session)
    ) -> Response:
        from constellation.studio import servers as srv

        server = studio.workspace.server(server_id)
        if server is None:
            raise HTTPException(404)
        try:
            data = await asyncio.to_thread(srv.computers, studio.state, server)
        except Exception as e:  # noqa: BLE001
            return render(request, "partials/computers.html", error=str(e), server=server)
        return render(
            request,
            "partials/computers.html",
            error="",
            server=server,
            devices=data.get("devices", []),
            you=data.get("you", ""),
            install_key=bool(data.get("install_key")),
        )

    @app.post("/servers/{server_id}/computers/{device_id}/remove")
    async def servers_computer_remove(
        request: Request,
        server_id: str,
        device_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
    ) -> Response:
        from urllib.parse import quote as _q

        from constellation.studio import servers as srv

        server = studio.workspace.server(server_id)
        if server is None or not re.fullmatch(r"[a-z0-9]{1,16}", device_id):
            raise HTTPException(404)
        try:
            await asyncio.to_thread(srv.remove_computer, studio.state, server, device_id)
        except Exception as e:  # noqa: BLE001
            return RedirectResponse("/servers?error=" + _q(f"Could not remove it: {e}"), 303)
        studio.state.audit("computer_removed", f"{server_id}:{device_id}", client_host(request))
        return RedirectResponse(f"/servers?ok=1#server-{server_id}", 303)

    @app.post("/servers/{server_id}/assign")
    async def servers_assign(
        request: Request,
        server_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        site_id: str = Form(...),
        on: str = Form(""),
    ) -> Response:
        ws = studio.workspace
        entry = ws.site(site_id)
        if ws.server(server_id) is None or entry is None:
            raise HTTPException(404)
        entry.server = server_id if on else ""
        save_workspace(ws)
        studio.state.audit(
            "site_assigned", f"{site_id}->{entry.server or '-'}", client_host(request)
        )
        return RedirectResponse(f"/servers?ok=1#server-{server_id}", 303)

    @app.post("/servers/{server_id}/connect")
    async def servers_connect(
        request: Request,
        server_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        site_id: str = Form(...),
        admin_token: str = Form(...),
    ) -> Response:
        from constellation.studio import servers as srv

        ws = studio.workspace
        server = ws.server(server_id)
        if server is None or ws.site(site_id) is None:
            raise HTTPException(404)
        try:
            srv.connect_site(
                studio.state,
                ws,
                server,
                site_id,
                admin_token.strip(),
                f"studio on {locks.machine_label()}",
            )
        except Exception as e:  # noqa: BLE001
            from urllib.parse import quote

            return RedirectResponse(f"/servers?error={quote(str(e))}", 303)
        studio.state.audit("server_connected", f"{server_id}:{site_id}", client_host(request))
        link = ""
        with contextlib.suppress(Exception):
            info = srv.server_status(server, admin_token.strip()).get("studio") or {}
            link = str(info.get("setup_link") or "")
        if link.startswith("https://"):
            studio.state.kv_set(f"studio_setup_link:{server_id}", link)
        return RedirectResponse("/servers?ok=1", 303)

    @app.post("/servers/{server_id}/forget")
    async def servers_forget(
        request: Request,
        server_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        site_id: str = Form(...),
    ) -> Response:
        from constellation.studio import servers as srv

        srv.forget_token(studio.state, server_id, site_id)
        return RedirectResponse("/servers?ok=1", 303)

    @app.post("/sites/{site_id}/publish-to-server")
    async def publish_to_server(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        mode: str = Form("plan"),
    ) -> Response:
        from constellation.studio import servers as srv

        root = studio.site_root(site_id)
        if mode == "apply":
            _check_writable(studio, site_id)
        ws = studio.workspace
        entry = ws.site(site_id)
        server = ws.server(entry.server) if entry and entry.server else None
        if server is None:
            raise HTTPException(400, "this site is not assigned to a server")
        try:
            job = await studio.jobs.start(
                "publish",
                site_id,
                lambda job: srv.run_publish(
                    job, studio.state, root, site_id, server, apply=mode == "apply"
                ),
            )
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        studio.state.audit("publish_" + mode, f"{site_id}->{server.id}", client_host(request))
        return render(request, "partials/job.html", job=job, site_id=site_id)

    @app.post("/sites/{site_id}/rollback")
    async def rollback_site(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        release_id: str = Form(...),
    ) -> Response:
        from constellation.studio import servers as srv

        _check_writable(studio, site_id)
        ws = studio.workspace
        entry = ws.site(site_id)
        server = ws.server(entry.server) if entry and entry.server else None
        if server is None:
            raise HTTPException(400, "this site is not assigned to a server")
        try:
            srv.rollback(studio.state, site_id, server, release_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(409, str(e)) from e
        studio.state.audit("rollback", f"{site_id}:{release_id}", client_host(request))
        return RedirectResponse(f"/sites/{site_id}/publish", 303)

    # ---- analytics --------------------------------------------------------------------------
    @app.get("/sites/{site_id}/analytics", response_class=HTMLResponse)
    async def analytics_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        days: int = 30,
    ) -> Response:
        from constellation.studio import servers as srv

        studio.site_root(site_id)
        ws = studio.workspace
        entry = ws.site(site_id)
        server = ws.server(entry.server) if entry and entry.server else None
        days = max(1, min(365, days))
        data = (
            srv.analytics(studio.state, site_id, server, days)
            if server
            else {"error": "This site is not assigned to a server."}
        )
        try:
            um = read_site_toml(studio.site_root(site_id)).get("analytics", {}).get("umami", {})
            code = umami.UmamiCode(
                str(um.get("script_url", "")),
                str(um.get("website_id", "")),
                str(um.get("host_url", "")),
            )
            umami_link = code.api_base if code.website_id and code.script_url else ""
        except Exception:  # noqa: BLE001 - a broken site.toml is reported elsewhere
            umami_link = ""
        return render(
            request,
            "analytics.html",
            site_id=site_id,
            server=server,
            data=data,
            days=days,
            umami_link=umami_link,
        )

    # ---- server administration (admin token per action) ---------------------------------------
    @app.post("/servers/{server_id}/admin")
    async def server_admin(
        request: Request,
        server_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        admin_token: str = Form(""),
        action: str = Form("status"),
    ) -> Response:
        from constellation.publish.client import PublishClient, PublishError
        from constellation.studio import servers as srv

        ws = studio.workspace
        server = ws.server(server_id)
        if server is None:
            raise HTTPException(404)
        form = await request.form()
        key = admin_token.strip() or srv.admin_key(studio.state, server.id)
        if not key:
            raise HTTPException(400, "this computer is not connected to that server")
        client = PublishClient(server.admin_url, key)
        result: dict[str, Any] = {}
        error = ""
        try:
            if action == "regenerate":
                result = client.regenerate_gemini_cert(str(form.get("host", "")))
            elif action == "config":
                changes = {
                    str(k)[2:]: str(v) for k, v in form.multi_items() if str(k).startswith("c.")
                }
                result = client.update_server_config(changes)
            status = client.status()
            certs = client.server_certs()
            config = client.server_config()
        except PublishError as e:
            error = str(e)
            status, certs, config = {}, {}, {}
        finally:
            client.close()
        studio.state.audit("server_admin", f"{server_id}:{action}", client_host(request))
        return render(
            request,
            "partials/server_admin.html",
            server=server,
            status=status,
            certs=certs,
            config=config,
            result=result,
            error=error,
            admin_token=admin_token.strip(),
        )

    # ---- theme install ------------------------------------------------------------------------
    @app.post("/themes/install")
    async def theme_install(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        file: UploadFile = Form(...),
        site_id: str = Form(""),
    ) -> Response:
        from constellation.core.themes import ThemeError, install_theme_zip

        data = await file.read()
        try:
            theme = install_theme_zip(studio.workspace_root / "themes", data)
        except (ThemeError, ValueError) as e:
            from urllib.parse import quote

            target = f"/sites/{site_id}/theme" if site_id else "/"
            return RedirectResponse(f"{target}?error={quote(str(e))}", 303)
        studio.state.audit("theme_installed", theme.name, client_host(request))
        return RedirectResponse(
            f"/sites/{site_id}/theme?installed={theme.name}" if site_id else "/", 303
        )

    # ---- doctor -------------------------------------------------------------------------------
    @app.get("/doctor", response_class=HTMLResponse)
    async def doctor(request: Request, sess: dict[str, Any] = Depends(require_session)) -> Response:
        from constellation.studio.doctor import run_checks

        checks = await asyncio.to_thread(
            run_checks,
            studio.state,
            studio.workspace_root,
            servers=studio.workspace.servers,
            running=True,
        )
        return render(request, "doctor.html", checks=checks)

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

    @app.post("/account/totp/begin")
    async def totp_begin(
        request: Request, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        from constellation.studio import totp as _totp

        secret = _totp.new_secret()
        studio.state.kv_set("totp_pending", secret)
        uri = _totp.provisioning_uri(secret, locks.machine_label())
        return render(
            request,
            "account.html",
            audit=studio.state.audit_tail(30),
            saved="",
            error="",
            pending_secret=secret,
            qr=_totp.qr_svg(uri),
        )

    @app.post("/account/totp/confirm")
    async def totp_confirm(
        request: Request, sess: dict[str, Any] = Depends(require_csrf), code: str = Form(...)
    ) -> Response:
        from constellation.studio import totp as _totp

        secret = str(studio.state.kv_get("totp_pending", "") or "")
        if not secret or not _totp.verify(secret, code):
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="That code did not match; scan again and try once more.",
                pending_secret=secret,
                qr=_totp.qr_svg(_totp.provisioning_uri(secret, locks.machine_label()))
                if secret
                else "",
            )
        studio.state.settings.totp_secret = secret
        studio.state.save_settings()
        studio.state.kv_set("totp_pending", "")
        studio.state.audit("totp_enabled", remote=client_host(request))
        return RedirectResponse("/account?saved=1", 303)

    @app.post("/account/totp/disable")
    async def totp_disable(
        request: Request, sess: dict[str, Any] = Depends(require_csrf), password: str = Form(...)
    ) -> Response:
        if not verify_password(studio.state.settings.password_hash, password):
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="Wrong password.",
            )
        if studio.state.settings.remote_mode:
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="Turn remote mode off before disabling the second factor.",
            )
        studio.state.settings.totp_secret = ""
        studio.state.save_settings()
        studio.state.audit("totp_disabled", remote=client_host(request))
        return RedirectResponse("/account?saved=1", 303)

    @app.post("/account/remote")
    async def remote_mode(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        enable: str = Form(""),
        bind: str = Form("0.0.0.0"),
        trust_proxy: str = Form(""),
        password: str = Form(...),
    ) -> Response:
        if not verify_password(studio.state.settings.password_hash, password):
            return render(
                request,
                "account.html",
                audit=studio.state.audit_tail(30),
                saved="",
                error="Wrong password.",
            )
        s = studio.state.settings
        if enable == "on":
            if not s.totp_secret and len(password) < REMOTE_PASSWORD_MIN:
                return render(
                    request,
                    "account.html",
                    audit=studio.state.audit_tail(30),
                    saved="",
                    error=f"Remote access without an authenticator needs a password of at least "
                    f"{REMOTE_PASSWORD_MIN} characters. Choose a longer password above, or add an "
                    "authenticator (recommended).",
                )
            s.remote_mode = True
            s.bind = bind.strip() or "0.0.0.0"
            s.trust_proxy = trust_proxy == "on"
        else:
            s.remote_mode = False
            s.bind = "127.0.0.1"
            s.trust_proxy = trust_proxy == "on"
        studio.state.save_settings()
        studio.state.delete_all_sessions()
        studio.state.audit("remote_mode", "on" if s.remote_mode else "off", client_host(request))
        resp = RedirectResponse("/account?saved=1&restart=1", 303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.post("/account/options")
    async def account_options(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        auto_login_local: str = Form(""),
    ) -> Response:
        studio.state.settings.auto_login_local = auto_login_local == "on"
        studio.state.save_settings()
        return RedirectResponse("/account?saved=1", 303)

    @app.post("/account/look")
    async def account_look(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        look: str = Form(...),
        mode: str = Form(looks.DEFAULT_MODE),
    ) -> Response:
        if look not in looks.LOOK_IDS or mode not in looks.MODE_IDS:
            raise HTTPException(400, "unknown look or mode")
        studio.state.settings.look = look
        studio.state.settings.mode = mode
        studio.state.save_settings()
        return RedirectResponse("/account?saved=1#look", 303)

    @app.post("/account/signout-everywhere")
    async def signout_everywhere(
        request: Request, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        studio.state.delete_all_sessions()
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/guide", response_class=HTMLResponse)
    async def guide(
        request: Request,
        sess: dict[str, Any] = Depends(require_session),
        host: str = "",
        ip: str = "",
        site: str = "",
        proxy: str = "caddy",
        port: int = 8180,
        gopher: str = "",
        studio_host: str = "",
    ) -> Response:
        """Step-by-step server setup and adding a site, with the user's names filled in."""

        def clean(value: str) -> str:
            # Hostnames and addresses only, so a pasted URL still yields a usable name.
            value = value.strip().lower().removeprefix("https://").removeprefix("http://")
            return re.sub(r"[^a-z0-9.:\-]", "", value.split("/")[0])[:253]

        g = {
            "host": clean(host),
            "ip": clean(ip),
            "site": clean(site),
            "proxy": proxy if proxy in ("caddy", "nginx", "none") else "caddy",
            "port": port if 1024 <= port <= 65535 else 8180,
            "gopher": gopher == "on" or not request.query_params,
            "studio": clean(studio_host),
        }
        g["host_or"] = g["host"] or "cms.example.org"
        g["ip_or"] = g["ip"] or "your server's IP address"
        g["site_or"] = g["site"] or "example.org"
        return render(request, "guide.html", g=g)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "app": studio.state.settings.machine_id}

    # ---- updates and the desktop app ---------------------------------------------------------
    @app.post("/updates/dismiss")
    async def updates_dismiss(
        request: Request, sess: dict[str, Any] = Depends(require_csrf), version: str = Form("")
    ) -> Response:
        studio.state.kv_set(updates.KV_DISMISSED, version)
        back = urlsplit(request.headers.get("referer", "/"))
        return RedirectResponse(
            safe_next(back.path + (f"?{back.query}" if back.query else "")), 303
        )

    @app.post("/updates/settings")
    async def updates_settings(
        request: Request,
        sess: dict[str, Any] = Depends(require_csrf),
        check: str = Form(""),
        now: str = Form(""),
    ) -> Response:
        studio.state.kv_set(updates.KV_OFF, check != "on")
        saved = "updates"
        if now and check == "on":
            latest = await asyncio.to_thread(updates.check, studio.state, force=True)
            saved = "update-found" if updates.available(studio.state) else "up-to-date"
            if latest is None:
                saved = "update-unknown"
        return RedirectResponse(f"/account?saved={saved}#updates", 303)

    # ---- GitHub (sign in, GitHub Pages) ------------------------------------------------------
    app.state.github_client = None  # tests put a fake GitHub here

    def _gh_next(value: str) -> str:
        return safe_next(value) if value.startswith("/") else "/account#github"

    @app.get("/github", response_class=HTMLResponse)
    async def github_page(
        request: Request,
        sess: dict[str, Any] = Depends(require_session),
        next: str = "",
        error: str = "",
    ) -> Response:
        return render(
            request,
            "github.html",
            gh_login=github.login(studio.state),
            next_url=_gh_next(next),
            error=error,
        )

    @app.post("/github/start")
    async def github_start(
        request: Request, sess: dict[str, Any] = Depends(require_csrf), next: str = Form("")
    ) -> Response:
        try:
            await asyncio.to_thread(github.begin, studio.state, client=app.state.github_client)
        except GitHubError as e:
            return RedirectResponse(
                f"/github?next={quote(_gh_next(next))}&error={quote(str(e))}", 303
            )
        return RedirectResponse(f"/github/code?next={quote(_gh_next(next))}", 303)

    @app.get("/github/code", response_class=HTMLResponse)
    async def github_code(
        request: Request, sess: dict[str, Any] = Depends(require_session), next: str = ""
    ) -> Response:
        pending = github.pending(studio.state)
        if pending is None:
            if github.token(studio.state):
                return RedirectResponse(_gh_next(next), 303)
            return RedirectResponse(f"/github?next={quote(_gh_next(next))}", 303)
        return render(request, "github_code.html", device=pending, next_url=_gh_next(next))

    @app.post("/github/poll")
    async def github_poll(
        request: Request, sess: dict[str, Any] = Depends(require_csrf), next: str = Form("")
    ) -> Response:
        try:
            status = await asyncio.to_thread(
                github.poll, studio.state, client=app.state.github_client
            )
        except GitHubError as e:
            return JSONResponse({"state": "error", "error": str(e)})
        if status == "done":
            studio.state.audit("github_signin", github.login(studio.state), client_host(request))
        pending = github.pending(studio.state)
        if request.headers.get("x-csrf-token") is None:  # the no-JavaScript button
            if status == "done":
                return RedirectResponse(_gh_next(next), 303)
            return RedirectResponse(f"/github/code?next={quote(_gh_next(next))}", 303)
        return JSONResponse(
            {
                "state": status,
                "next": _gh_next(next),
                "interval": int(pending["interval"]) if pending else 5,
            }
        )

    @app.post("/github/signout")
    async def github_signout(
        request: Request, sess: dict[str, Any] = Depends(require_csrf)
    ) -> Response:
        studio.state.audit("github_signout", github.login(studio.state), client_host(request))
        github.sign_out(studio.state)
        return RedirectResponse("/account?saved=github#github", 303)

    @app.get("/sites/{site_id}/github", response_class=HTMLResponse)
    async def github_pages_page(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_session),
        wizard: str = "",
    ) -> Response:
        return _github_pages(request, site_id, wizard=bool(wizard))

    def _github_pages(
        request: Request,
        site_id: str,
        *,
        wizard: bool,
        repo: str = "",
        error: str = "",
        exists: bool = False,
        job: Any = None,
        status: int = 200,
    ) -> HTMLResponse:
        root = studio.site_root(site_id)
        account = github.login(studio.state)
        try:
            domain = github.own_domain(root)
        except ContentError as e:
            domain, error = "", error or str(e)
        entry = studio.workspace.site(site_id)
        repo = repo or (github.suggested_repo(site_id, account, domain) if account else "")
        response = render(
            request,
            "github_pages.html",
            site_id=site_id,
            wizard_site=site_id if wizard else "",
            wizard=wizard,
            site_name=entry.name if entry else site_id,
            gh_login=account,
            domain=domain,
            repo=repo,
            address=github.site_address(account, repo, domain) if account else "",
            error=error,
            exists=exists,
            job=job,
            read_only=studio.read_only_reason(site_id),
        )
        response.status_code = status
        return response

    @app.post("/sites/{site_id}/github", response_class=HTMLResponse)
    async def github_pages_submit(
        request: Request,
        site_id: str,
        sess: dict[str, Any] = Depends(require_csrf),
        repo: str = Form(""),
        confirm: str = Form(""),
        wizard: str = Form(""),
    ) -> Response:
        root = studio.site_root(site_id)
        _check_writable(studio, site_id)
        account = github.login(studio.state)
        if not account:
            return RedirectResponse(f"/sites/{site_id}/github", 303)
        repo = repo.strip()
        domain = github.own_domain(root)
        problem = github.check_repo_name(repo, account, domain)
        if problem:
            return _github_pages(
                request, site_id, wizard=bool(wizard), repo=repo, error=problem, status=400
            )
        if not confirm:
            api = GitHubAPI(github.token(studio.state), client=app.state.github_client)
            try:
                existing = await asyncio.to_thread(api.repo, account, repo)
            except GitHubError as e:
                return _github_pages(
                    request, site_id, wizard=bool(wizard), repo=repo, error=str(e), status=400
                )
            finally:
                api.close()
            if existing is not None and int(existing.get("size") or 0) > 0:
                return _github_pages(request, site_id, wizard=bool(wizard), repo=repo, exists=True)
        try:
            job = await studio.jobs.start(
                "GitHub Pages",
                site_id,
                lambda job: github.setup_pages(
                    job, studio.state, root, site_id, repo, client=app.state.github_client
                ),
            )
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        studio.state.audit("github_pages", f"{site_id}->{account}/{repo}", client_host(request))
        return _github_pages(request, site_id, wizard=bool(wizard), repo=repo, job=job)

    @app.post("/app/quit", response_class=HTMLResponse)
    async def app_quit(request: Request, sess: dict[str, Any] = Depends(require_csrf)) -> Response:
        on_quit = app.state.on_quit
        if on_quit is None:
            raise HTTPException(404)
        asyncio.get_running_loop().call_later(0.5, on_quit)
        return HTMLResponse(
            "<!DOCTYPE html><meta charset=utf-8><title>Closed</title>"
            "<p style='font:1.1rem system-ui;margin:3rem'>ConstellationCMS has closed. "
            "Open it again from the Start menu or your applications.</p>"
        )

    return app


# ---- helpers ------------------------------------------------------------------------------------
def _asset_version(static: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for name in ("studio.css", "studio.js", "editor.js"):
        with contextlib.suppress(OSError):
            digest.update((static / name).read_bytes())
    return digest.hexdigest()[:10]


def _admin_url(value: str) -> str:
    """A server's admin address; a bare https://host gets the server's API path added."""
    from urllib.parse import urlsplit

    url = value.strip().rstrip("/")
    return url + "/_constellation" if url and not urlsplit(url).path else url


def _local_csrf(studio: Studio) -> str:
    token = studio.state.kv_get("local_csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        studio.state.kv_set("local_csrf", token)
    return str(token)


# Plain descriptions of the built-in themes for the first-run wizard, in the order shown.
THEME_BLURBS: dict[str, tuple[str, str]] = {
    "base": ("Simple", "Clean and easy to read on any screen."),
    "paper": ("Paper", "Warm paper colours and classic book type."),
    "fractum": ("Terminal", "Dark and typewriter-like, like an old computer screen."),
    "nebula": ("Neon", "Bright magenta on black, with a soft glow."),
}


def _free_site_id(studio: Studio, name: str) -> str:
    """A folder name for a new site: from its name, with a number added if it's taken."""
    from slugify import slugify

    base = slugify(name)[:40].strip("-") or "site"
    if not re.match(r"^[a-z0-9]", base):
        base = "site-" + base
    ws = studio.workspace
    sid, n = base, 2
    while ws.site(sid) is not None or ws.site_dir(sid).exists():
        sid, n = f"{base}-{n}", n + 1
    return sid


def _valid_timezone(name: str) -> str:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        return "UTC"
    return name or "UTC"


def _after_setup(studio: Studio) -> str:
    """Straight into making a first site, unless the sites folder already has some."""
    return "/" if any(not s.archived for s in studio.workspace.sites) else "/welcome"


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
        # Secure whenever the browser reached us over HTTPS. Remote mode over a private
        # network such as Tailscale or WireGuard is plain HTTP, where a Secure cookie would
        # never be sent back.
        secure=request.url.scheme == "https"
        or (
            studio.state.settings.trust_proxy
            and request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() == "https"
        ),
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


def _missing_media_warnings(root: Path, rel: str, doc: Any, platform: str) -> list[dict[str, Any]]:
    """The editor's view of the build's LINK-BROKEN-MEDIA check, for unsaved text."""
    import posixpath

    from constellation.core.markdown.document import Warning as DocWarning

    if not rel:
        return []
    try:
        source = content_ops._safe(root, rel)
    except ValueError:
        return []
    bundle = source.parent if source.name == "index.md" else None
    out: list[dict[str, Any]] = []
    for ref in doc.refs:
        if ref.kind != "media":
            continue
        if ref.target.startswith("/"):
            found = (root / "media" / ref.target.lstrip("/")).is_file()
        else:
            found = bundle is not None and (bundle / posixpath.normpath(ref.target)).is_file()
        if not found:
            out.append(
                DocWarning(
                    "LINK-BROKEN-MEDIA",
                    "error",
                    platform,
                    0,
                    f"media {ref.target!r} not found; upload it or fix the name",
                ).as_dict()
            )
    return out


EXPORT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _export_problem(name: str, platform: str, target: str, layout: str, taken: list[str]) -> str:
    if not EXPORT_NAME.match(name):
        return "the name must be lowercase letters, digits, - or _ (for example vps-web)"
    if name in taken:
        return f"there is already a target called {name!r}"
    if platform not in ("web", "gemini", "gopher"):
        return "unknown platform"
    if layout not in ("plain", "agate"):
        return "unknown layout"
    if target.startswith("folder:"):
        folder = target[len("folder:") :].strip()
        if not Path(folder).expanduser().is_absolute():
            return "a folder target needs a full path, such as folder:C:\\sites\\out or folder:/srv/out"
        return ""
    if target.startswith("sftp://") or target.startswith("rsync:"):
        if any(c in target for c in " \t\n\"'`;|&$"):
            return "the target must not contain spaces or shell characters"
        return ""
    if is_git_target(target):
        if platform != "web":
            return "GitHub Pages and git targets publish the web platform only"
        try:
            parse_git_spec(target)
        except ValueError as e:
            return str(e)
        return ""
    return "the target must start with folder:, sftp://, rsync:, github: or git:"


def build_hosts(root: Path, platform: str) -> list[str]:
    from constellation.core.content import load_config

    try:
        pcfg = load_config(root).platform(platform)
    except ContentError:
        return []
    hosts = list(pcfg.hostnames)
    if pcfg.base_url:
        host = urlsplit(pcfg.base_url).hostname
        if host:
            hosts.append(host)
    return hosts


def _site_timezone(root: Path) -> Any:
    from constellation.core.content import load_config, site_tz

    try:
        return site_tz(load_config(root).site.timezone)
    except ContentError:
        return UTC


def _date_for_input(value: Any, tz: Any) -> str:
    """A front-matter date as a ``datetime-local`` value, in the site's time zone."""
    from datetime import date as date_type

    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return str(value)[:16]
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz)
        return str(value.strftime("%Y-%m-%dT%H:%M"))
    if isinstance(value, date_type):
        return f"{value:%Y-%m-%d}T00:00"
    return str(value)[:16]


def _merge_meta(original: dict[str, Any], form: dict[str, Any], tz: Any) -> dict[str, Any]:
    """Apply the editor form to the file's front matter.

    Keys the form does not send are kept as they are, so fields the editor does not show
    (``updated``, ``lang``, per-platform overrides, theme keys) survive every save. A field
    the form shows unchanged keeps its original value and type.
    """
    out = dict(original)
    for k, v in form.items():
        if v is None:
            continue
        if k == "seo" and isinstance(v, dict):
            seo = dict(out.get("seo") or {})
            for kk, vv in v.items():
                if vv is None:
                    continue
                if vv in ("", False):
                    seo.pop(kk, None)
                else:
                    seo[kk] = vv
            if seo:
                out["seo"] = seo
            else:
                out.pop("seo", None)
        elif k in META_LIST_KEYS:
            before = original.get(k)
            if isinstance(before, list) and str(v).strip() == ", ".join(str(x) for x in before):
                continue
            values = [x.strip() for x in str(v).split(",") if x.strip()]
            if values:
                out[k] = values
            else:
                out.pop(k, None)
        elif k == "date":
            if v == _date_for_input(original.get("date"), tz):
                continue
            if v:
                out[k] = v
            else:
                out.pop(k, None)
        elif k in ("series_part", "menu_order"):
            if v == "":
                out.pop(k, None)
                continue
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                continue
        elif k == "featured":
            # Written only when on, so ordinary posts keep a short front matter.
            if v is True:
                out[k] = True
            else:
                out.pop(k, None)
        elif k == "comments":
            if v is False:
                out[k] = False
            else:
                out.pop(k, None)
        elif k == "toc":
            if v in ("true", True):
                out[k] = True
            elif v in ("false", False):
                out[k] = False
            else:
                out.pop(k, None)
        elif v == "":
            out.pop(k, None)
        else:
            out[k] = v
    return out


def _validate_meta(rel: str, meta: dict[str, Any]) -> str:
    """Refuse to write front matter the site loader would reject."""
    from pydantic import ValidationError

    from constellation.core.models import PageMeta, PostMeta

    model = PostMeta if "/posts/" in "/" + rel.replace("\\", "/") else PageMeta
    try:
        model.model_validate(meta)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "front matter"
        return f"not saved: {where}: {first.get('msg', 'invalid')}"
    return ""


def _meta_from_form(form: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for k, v in form.items():
        if v in (None, ""):
            continue
        if k in META_LIST_KEYS:
            meta[k] = [x.strip() for x in str(v).split(",") if x.strip()]
        elif k == "toc":
            if v in ("true", "false", True, False):
                meta[k] = v in ("true", True)
        elif k in ("featured", "comments"):
            meta[k] = bool(v)
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


def _settings_extras(root: Path) -> dict[str, Any]:
    """Share networks and plugins for the settings page."""
    from constellation.core import plugins
    from constellation.core.build.features import SHARE_LABELS
    from constellation.core.models import SHARE_NETWORKS

    doc = read_site_toml(root)
    pl = doc.get("plugins", {})
    settings = pl.get("settings", {}) if hasattr(pl, "get") else {}
    folder = root.parent.parent / "plugins" if root.parent.name == "sites" else root / "plugins"
    return {
        "share_networks": [(n, SHARE_LABELS[n]) for n in SHARE_NETWORKS],
        "plugins_available": plugins.available(root),
        "plugins_enabled": list(pl.get("enabled", [])) if hasattr(pl, "get") else [],
        "plugin_settings": {k: dict(v) for k, v in settings.items()},
        "plugins_folder": str(folder),
    }


def _coerce(dotted: str, value: str) -> Any:
    key = dotted.rsplit(".", 1)[-1]
    if key in ("enabled", "image_link", "toc", "lightbox", "search", "pin_featured"):
        return value == "on"
    if key in (
        "pagination",
        "port",
        "long_cell_chars",
        "wide_table_cols",
        "width_map",
        "width_text",
        "toc_min_headings",
        "related_posts",
    ):
        try:
            number = int(value)
        except ValueError:
            raise ValueError(f"{dotted} must be a whole number, not {value!r}") from None
        if number < (0 if key == "related_posts" else 1):
            raise ValueError(f"{dotted} must be at least {0 if key == 'related_posts' else 1}")
        return number
    if key in ("hostnames", "feeds", "display"):
        return [x.strip() for x in value.split(",") if x.strip()]
    if key in ("items", "redirects"):
        if not value.strip():
            return []
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{dotted} is not valid JSON ({e.msg} at line {e.lineno})") from None
    return value


def _validate_site_doc(doc: Any) -> None:
    """Refuse a site.toml the site loader would reject."""
    from zoneinfo import ZoneInfo

    from pydantic import ValidationError

    from constellation.core.models import SiteConfig

    data = json.loads(json.dumps(doc.unwrap() if hasattr(doc, "unwrap") else doc, default=str))
    try:
        cfg = SiteConfig.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ()))
        raise ValueError(f"{where}: {first.get('msg', 'invalid')}") from None
    try:
        ZoneInfo(cfg.site.timezone)
    except Exception:  # noqa: BLE001
        raise ValueError(
            f"unknown timezone {cfg.site.timezone!r}; use a name like America/Chicago"
        ) from None


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
