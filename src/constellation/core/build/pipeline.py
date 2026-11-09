"""The build: site → one output tree per platform, plus a manifest with hashes."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slugify import slugify

from constellation.core import feeds
from constellation.core.build.images import ImageSet, make_variants
from constellation.core.markdown.document import Document, Ref, Warning
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import Page, Post, Site, Term
from constellation.core.render.gemtext import GemtextRenderer
from constellation.core.render.gopher import GopherRenderer, GopherTarget, info_line, item_for_url
from constellation.core.render.html import HtmlRenderer
from constellation.core.render.plaintext import summary_text
from constellation.core.routes import (
    RefResolver,
    absolute_url,
    index_output_file,
    index_path,
    media_path,
    output_file,
    platform_path,
    url_for,
)
from constellation.core.themes import Theme, load_theme, make_environment, theme_chain

MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".gmi": "text/gemini",
    ".txt": "text/plain; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".xml": "application/xml",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".atom": "application/atom+xml",
}


@dataclass(slots=True)
class BuiltFile:
    path: str
    data: bytes
    media_type: str
    source: Path | None = None  # set for copied media (large files are not held in memory twice)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.bytes()).hexdigest()

    def bytes(self) -> bytes:
        if self.source is not None:
            return self.source.read_bytes()
        return self.data


@dataclass(slots=True)
class PlatformBuild:
    platform: str
    files: dict[str, BuiltFile] = field(default_factory=dict)
    warnings: list[Warning] = field(default_factory=list)
    item_warnings: dict[str, list[Warning]] = field(default_factory=dict)

    def add(
        self,
        path: str,
        data: bytes | str,
        media_type: str | None = None,
        *,
        source: Path | None = None,
    ) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        mt = media_type or media_type_for(path)
        if self.platform == "gemini" and mt == "text/gemini":
            mt = "text/gemini"
        self.files[path] = BuiltFile(path, raw, mt, source)

    def manifest(self) -> dict[str, dict[str, Any]]:
        return {
            p: {"sha256": f.sha256, "size": len(f.bytes()), "type": f.media_type}
            for p, f in sorted(self.files.items())
        }


@dataclass(slots=True)
class Build:
    site_id: str
    now: datetime
    platforms: dict[str, PlatformBuild] = field(default_factory=dict)

    @property
    def warnings(self) -> list[Warning]:
        return [w for p in self.platforms.values() for w in p.warnings]

    def manifest(self) -> dict[str, Any]:
        return {
            "version": 1,
            "site": self.site_id,
            "built_at": self.now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "platforms": {name: pb.manifest() for name, pb in sorted(self.platforms.items())},
        }

    def release_id(self) -> str:
        """Hash of the content only, so an identical rebuild is the same release."""
        payload = json.dumps(
            self.manifest()["platforms"], sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


def media_type_for(path: str) -> str:
    if Path(path).name == "gophermap":
        return "text/plain; charset=utf-8"
    ext = Path(path).suffix.lower()
    if ext in MEDIA_TYPES:
        return MEDIA_TYPES[ext]
    guess, _ = mimetypes.guess_type(path)
    return guess or "application/octet-stream"


@dataclass(slots=True)
class Rendered:
    """One content item rendered for one platform."""

    body: str
    summary: str
    warnings: list[Warning]
    doc: Document


def build_site(
    site: Site,
    *,
    platforms: list[str] | None = None,
    now: datetime | None = None,
    include_drafts: bool = False,
    theme_search: list[Path] | None = None,
) -> Build:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cfg = site.config
    wanted = platforms or cfg.enabled_platforms()
    theme = load_theme(cfg.site.theme, search=theme_search)
    chain = theme_chain(theme, search=theme_search)
    overrides = site.root / "theme"
    build = Build(site_id=site.id, now=now)

    listed = site.published_posts(now)
    visible = site.visible_posts(now)
    if include_drafts:
        drafts = [p for p in site.posts if p.meta.status == "draft" or p.date > now]
        visible = sorted(visible + drafts, key=lambda p: (p.date, p.slug), reverse=True)
    pages = [p for p in site.pages if p.meta.status != "draft" or include_drafts]
    resolver = RefResolver(
        cfg, post_slugs={p.slug for p in site.posts}, page_slugs={p.slug for p in site.pages}
    )
    docs = _parse_all(visible, pages, resolver)
    link_problems = _check_links(site, docs, visible, pages)

    image_sets = _image_sets(visible, pages, site) if "web" in wanted else {}

    for platform in wanted:
        if platform not in ("web", "gemini", "gopher"):
            continue
        pb = PlatformBuild(platform)
        pb.warnings.extend(link_problems)
        env = make_environment(chain, platform, overrides=overrides if overrides.is_dir() else None)
        ctx = _Context(
            site, platform, env, chain, build, listed, docs, image_sets if platform == "web" else {}
        )
        for post in visible:
            ctx.render_post(post, pb)
        for page in pages:
            ctx.render_page(page, pb)
        ctx.render_home(pb)
        ctx.render_terms(pb)
        ctx.render_archives(pb)
        ctx.render_feeds(pb)
        ctx.copy_media(visible, pages, pb)
        if platform == "web":
            ctx.copy_static(pb)
            ctx.render_404(pb)
            for iset in image_sets.values():
                for v in iset.variants:
                    pb.add(v.path.lstrip("/"), v.data, v.media_type)
        build.platforms[platform] = pb
    return build


def _image_sets(posts: list[Post], pages: list[Page], site: Site) -> dict[str, ImageSet]:
    sets: dict[str, ImageSet] = {}
    for item in [*posts, *pages]:
        for m in item.media:
            url = media_path(item.slug if item.kind == "post" else None, m.name)
            iset = make_variants(m, url)
            if iset is not None:
                sets[url] = iset
    site_media = site.root / "media"
    if site_media.is_dir():
        for f in sorted(site_media.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                url = "/media/" + f.relative_to(site_media).as_posix()
                iset = make_variants(f, url)
                if iset is not None:
                    sets[url] = iset
    return sets


def _parse_all(posts: list[Post], pages: list[Page], resolver: RefResolver) -> dict[str, Document]:
    docs: dict[str, Document] = {}

    def resolve(href: str, is_image: bool) -> Ref:
        return resolver.resolve(href, is_image=is_image)

    for item in [*posts, *pages]:
        docs[f"{item.kind}:{item.slug}"] = parse_markdown(item.body, resolve=resolve)
    return docs


def _check_links(
    site: Site, docs: dict[str, Document], visible: list[Post], pages: list[Page]
) -> list[Warning]:
    """Links to drafts, future posts, missing pages or missing media are errors."""
    post_slugs = {p.slug for p in visible}
    page_slugs = {p.slug for p in pages}
    problems: list[Warning] = []
    for key, doc in docs.items():
        kind, _, slug = key.partition(":")
        item = site.post_by_slug(slug) if kind == "post" else site.page_by_slug(slug)
        media_names = {m.name for m in item.media} if item else set()
        site_media = site.root / "media"
        for ref in doc.refs:
            if ref.kind == "post" and ref.target not in post_slugs:
                code = (
                    "LINK-UNPUBLISHED" if site.post_by_slug(ref.target) else "LINK-BROKEN-INTERNAL"
                )
                problems.append(
                    Warning(
                        code,
                        "error",
                        "all",
                        0,
                        f"{key}: link to post {ref.target!r} which {'is not published' if code == 'LINK-UNPUBLISHED' else 'does not exist'}",
                    )
                )
            elif ref.kind == "page" and ref.target not in page_slugs:
                problems.append(
                    Warning(
                        "LINK-BROKEN-INTERNAL",
                        "error",
                        "all",
                        0,
                        f"{key}: link to page {ref.target!r} which does not exist",
                    )
                )
            elif ref.kind == "media":
                if ref.target.startswith("/"):
                    if not (site_media / ref.target.lstrip("/")).is_file():
                        problems.append(
                            Warning(
                                "LINK-BROKEN-MEDIA",
                                "error",
                                "all",
                                0,
                                f"{key}: site media {ref.target!r} not found",
                            )
                        )
                elif ref.target not in media_names:
                    problems.append(
                        Warning(
                            "LINK-BROKEN-MEDIA",
                            "error",
                            "all",
                            0,
                            f"{key}: media {ref.target!r} not found next to the post",
                        )
                    )
    return problems


class _Context:
    def __init__(
        self,
        site: Site,
        platform: str,
        env: Any,
        chain: list[Theme],
        build: Build,
        listed: list[Post],
        docs: dict[str, Document],
        image_sets: dict[str, ImageSet],
    ) -> None:
        self.site = site
        self.image_sets = image_sets
        self.bodies: dict[str, str] = {}
        self._gopher_text = ""
        self.summaries: dict[str, str] = {}
        self.cfg = site.config
        self.platform = platform
        self.env = env
        self.chain = chain
        self.build = build
        self.listed = listed
        self.docs = docs
        self.theme_settings: dict[str, Any] = {}
        for t in reversed(chain):
            self.theme_settings.update(t.defaults())
        user_settings = site.config.theme.get("settings")
        if isinstance(user_settings, dict):
            self.theme_settings.update({str(k): v for k, v in user_settings.items()})
        self._install_globals()

    # ---- template environment -------------------------------------------------------------
    def gopher_target(self) -> GopherTarget:
        from urllib.parse import urlsplit

        pcfg = self.cfg.platform("gopher")
        host = (
            pcfg.hostnames[0] if pcfg.hostnames else urlsplit(pcfg.base_url).hostname
        ) or "localhost"
        return GopherTarget(host, pcfg.port or 70)

    def _install_globals(self) -> None:
        cfg, platform = self.cfg, self.platform

        def url(kind: str, slug: str = "", **extra: str) -> str:
            return platform_path(cfg, platform, kind, slug, **extra)

        def menu_url(item: Any) -> str:
            if getattr(item, "url", None):
                return str(item.url)
            ref = getattr(item, "ref", None) or "home"
            if ref == "home":
                return "/"
            kind, _, slug = ref.partition(":")
            return platform_path(cfg, platform, kind, slug)

        def asset(name: str) -> str:
            return f"/assets/{self.chain[0].name}/{name}"

        self.env.globals.update(
            {
                "site": self.site,
                "platform": platform,
                "url_for": url,
                "menu_url": menu_url,
                "absolute_url": lambda path: absolute_url(cfg, platform, path),
                "asset": asset,
                "theme": self.theme_settings,
                "now": self.build.now,
                "hr": cfg.render.gemini.hr,
                "gopher_item": self._gopher_item,
                "gopher_info": lambda text: info_line(
                    str(text), self.gopher_target().host, self.gopher_target().port
                ),
            }
        )
        self.env.filters["date"] = _date_filter(cfg)
        self.env.filters["isoformat"] = lambda d: d.isoformat()
        self.env.filters["slug"] = lambda s: slugify(str(s))
        from markupsafe import Markup

        from constellation.core.numerals import render_numeral

        def numeral(n: object, system: str | None = None) -> object:
            text = render_numeral(
                int(str(n)),
                system or cfg.calendar.numerals,
                platform,
                text_fallback=cfg.calendar.numerals_text_fallback,
            )
            return Markup(text) if platform == "web" else text

        self.env.filters["numeral"] = numeral
        self.env.filters["ddate"] = lambda d, fmt="%{%A, %B %d%}, %Y YOLD": _ddate(d, fmt, cfg)

    def _render_body(self, item: Post | Page) -> Rendered:
        doc = self.docs[f"{item.kind}:{item.slug}"]
        slug = item.slug if item.kind == "post" else None

        def u(ref: Ref) -> str:
            return url_for(ref, self.platform, self.cfg, item_slug=slug)

        if self.platform == "gemini":
            res = GemtextRenderer(self.cfg.render.gemini, u).render(doc)
        elif self.platform == "gopher":
            renderer = GopherRenderer(self.cfg.render.gopher, u, self.gopher_target())
            res = renderer.render(doc)
            self._gopher_text = renderer.render_text(doc).text
        else:
            srcsets = {
                path: iset.srcset() for path, iset in self.image_sets.items() if iset.srcset()
            }
            res = HtmlRenderer(u, image_srcset=srcsets).render(doc)
        summary = item.meta.summary or summary_text(doc)
        if item.kind == "post":
            self.bodies[item.slug] = res.text
            self.summaries[item.slug] = summary
        return Rendered(res.text, summary, res.warnings, doc)

    def _common(self, item: Post | Page, rendered: Rendered) -> dict[str, Any]:
        author_id = item.meta.author or self.cfg.site.author
        author = self.cfg.author_by_id(author_id) if author_id else None
        web = self.cfg.platform("web")
        web_url = (
            (web.base_url + platform_path(self.cfg, "web", item.kind, item.slug))
            if web.enabled and web.base_url
            else ""
        )
        gem = self.cfg.platform("gemini")
        gem_path = platform_path(self.cfg, "gemini", item.kind, item.slug) if gem.enabled else ""
        return {
            "item": item,
            "body": rendered.body,
            "summary": rendered.summary,
            "author": author,
            "web_url": web_url,
            "gemini_path": gem_path,
        }

    def render_post(self, post: Post, pb: PlatformBuild) -> None:
        rendered = self._render_body(post)
        key = f"post:{post.slug}"
        pb.item_warnings[key] = rendered.warnings
        pb.warnings.extend(rendered.warnings)
        ctx = self._common(post, rendered)
        idx = next((i for i, p in enumerate(self.listed) if p.slug == post.slug), None)
        ctx["prev_post"] = (
            self.listed[idx + 1] if idx is not None and idx + 1 < len(self.listed) else None
        )
        ctx["next_post"] = self.listed[idx - 1] if idx is not None and idx > 0 else None
        ctx["featured_image"] = media_path(post.slug, post.meta.image) if post.meta.image else ""
        ctx["featured_srcset"] = (
            self.image_sets[ctx["featured_image"]].srcset()
            if ctx["featured_image"] in self.image_sets
            else ""
        )
        series = None
        if post.meta.series:
            term = self.site.series.get(slugify(post.meta.series))
            if term:
                part = post.meta.series_part or (
                    term.posts.index(post) + 1 if post in term.posts else None
                )
                series = {"slug": term.slug, "title": term.title, "part": part}
        ctx["series"] = series
        if self.platform == "gopher":
            ctx["text_selector"] = (
                platform_path(self.cfg, "gopher", "post", post.slug).rstrip("/") + ".txt"
            )
            text = self.env.get_template("post.map").render(**ctx)
            pb.add(output_file(self.cfg, "gopher", "post", post.slug), _tidy(text, "gopher"))
            txt = self.env.get_template("post.txt").render(**{**ctx, "body": self._gopher_text})
            pb.add(ctx["text_selector"].lstrip("/"), _tidy(txt, "gopher"))
            return
        text = self.env.get_template(
            "post.gmi" if self.platform == "gemini" else "post.html"
        ).render(**ctx)
        pb.add(output_file(self.cfg, self.platform, "post", post.slug), _tidy(text, self.platform))

    def render_page(self, page: Page, pb: PlatformBuild) -> None:
        rendered = self._render_body(page)
        key = f"page:{page.slug}"
        pb.item_warnings[key] = rendered.warnings
        pb.warnings.extend(rendered.warnings)
        ctx = self._common(page, rendered)
        name = page.meta.template or "page"
        ext = {"gemini": "gmi", "gopher": "map"}.get(self.platform, "html")
        try:
            template = self.env.get_template(f"{name}.{ext}")
        except Exception:
            template = self.env.get_template(f"page.{ext}")
        text = template.render(**ctx)
        pb.add(output_file(self.cfg, self.platform, "page", page.slug), _tidy(text, self.platform))

    def _summaries(self) -> dict[str, str]:
        return {
            p.slug: (p.meta.summary or summary_text(self.docs[f"post:{p.slug}"]))
            for p in self.listed
        }

    def _tpl(self, name: str) -> Any:
        ext = {"gemini": "gmi", "gopher": "map"}.get(self.platform, "html")
        return self.env.get_template(f"{name}.{ext}")

    def _gopher_item(self, item_type: str, text: object, selector: str) -> str:
        target = self.gopher_target()
        if not selector.startswith("URL:"):
            selector = selector.rstrip("/") or "/"
        if item_type == "auto":
            return item_for_url(selector, str(text), target)
        return f"{item_type}{str(text).replace(chr(9), '    ')}\t{selector}\t{target.host}\t{target.port}"

    def render_terms(self, pb: PlatformBuild) -> None:
        summaries = self._summaries()
        listed_slugs = {p.slug for p in self.listed}
        groups: list[tuple[str, dict[str, Term], str]] = [
            ("tag", self.site.tags, "Tags"),
            ("category", self.site.categories, "Categories"),
            ("series", self.site.series, "Series"),
        ]
        for kind, terms, heading in groups:
            shown: list[Term] = []
            for term in terms.values():
                posts = [p for p in term.posts if p.slug in listed_slugs]
                if kind != "series":
                    posts = sorted(posts, key=lambda p: (p.date, p.slug), reverse=True)
                if not posts and not term.body:
                    continue
                shown.append(term)
                term_body = ""
                if term.body:
                    doc = parse_markdown(term.body)
                    term_body = (
                        GemtextRenderer(
                            self.cfg.render.gemini, lambda r: url_for(r, "gemini", self.cfg)
                        )
                        .render(doc)
                        .text
                        if self.platform == "gemini"
                        else HtmlRenderer(lambda r: url_for(r, "web", self.cfg)).render(doc).text
                    )
                text = self._tpl("list").render(
                    term=term,
                    posts=posts,
                    summaries=summaries,
                    term_body=term_body,
                    gemini_path=platform_path(self.cfg, "gemini", kind, term.slug),
                )
                pb.add(
                    output_file(self.cfg, self.platform, kind, term.slug),
                    _tidy(text, self.platform),
                )
            if shown or kind != "series":
                text = self._tpl("terms").render(
                    kind=kind,
                    terms=shown,
                    heading=heading,
                    gemini_path=index_path(self.cfg, "gemini", kind),
                )
                pb.add(index_output_file(self.cfg, self.platform, kind), _tidy(text, self.platform))

    def render_archives(self, pb: PlatformBuild) -> None:
        by_year: dict[int, list[Post]] = {}
        for p in self.listed:
            by_year.setdefault(p.date.year, []).append(p)
        years = sorted(((y, len(ps)) for y, ps in by_year.items()), reverse=True)
        text = self._tpl("archive").render(
            year=None,
            years=years,
            posts=self.listed,
            gemini_path=index_path(self.cfg, "gemini", "archive"),
        )
        pb.add(index_output_file(self.cfg, self.platform, "archive"), _tidy(text, self.platform))
        for y, posts in by_year.items():
            text = self._tpl("archive").render(
                year=y,
                years=years,
                posts=posts,
                gemini_path=platform_path(self.cfg, "gemini", "archive", "", year=str(y)),
            )
            pb.add(
                output_file(self.cfg, self.platform, "archive", "", year=str(y)),
                _tidy(text, self.platform),
            )

    def render_feeds(self, pb: PlatformBuild) -> None:
        pcfg = self.cfg.platform(self.platform)
        if "atom" in pcfg.feeds:
            pb.add(
                "feed.xml",
                feeds.atom_feed(
                    self.site,
                    self.listed,
                    platform=self.platform,
                    now=self.build.now,
                    bodies=self.bodies,
                    summaries=self.summaries,
                ),
                "application/atom+xml",
            )
        if self.platform == "web":
            if "json" in pcfg.feeds:
                pb.add(
                    "feed.json",
                    feeds.json_feed(
                        self.site,
                        self.listed,
                        now=self.build.now,
                        bodies=self.bodies,
                        summaries=self.summaries,
                    ),
                    "application/feed+json",
                )
            entries: list[tuple[str, datetime | None]] = [
                ("/", self.listed[0].updated or self.listed[0].date if self.listed else None)
            ]
            for p in self.listed:
                if not p.meta.seo.noindex:
                    entries.append(
                        (platform_path(self.cfg, "web", "post", p.slug), p.updated or p.date)
                    )
            for pg in self.site.pages:
                if pg.meta.status == "published":
                    entries.append((platform_path(self.cfg, "web", "page", pg.slug), pg.updated))
            for kind, terms in (
                ("tag", self.site.tags),
                ("category", self.site.categories),
                ("series", self.site.series),
            ):
                for t in terms.values():
                    entries.append((platform_path(self.cfg, "web", kind, t.slug), None))
            pb.add("sitemap.xml", feeds.sitemap(self.site, entries), "application/xml")
            pb.add("robots.txt", feeds.robots_web(self.site), "text/plain; charset=utf-8")
            for name in ("favicon.ico", "favicon.png", "favicon.svg"):
                src = self.site.root / "media" / name
                if src.is_file():
                    pb.add(name, b"", media_type_for(name), source=src)
        elif self.platform == "gemini":
            pb.add("robots.txt", feeds.robots_gemini(), "text/plain")
            pb.add("favicon.txt", feeds.favicon_txt(self.site), "text/plain")

    def render_home(self, pb: PlatformBuild) -> None:
        summaries = self._summaries()
        if self.platform == "gemini":
            text = self.env.get_template("home.gmi").render(
                posts=self.listed, summaries=summaries, gemini_path="/"
            )
            pb.add("index.gmi", _tidy(text, "gemini"))
            return
        if self.platform == "gopher":
            text = self.env.get_template("home.map").render(
                posts=self.listed, summaries=summaries, gemini_path="/"
            )
            pb.add("gophermap", _tidy(text, "gopher"))
            return
        per_page = max(1, self.cfg.platform("web").pagination)
        pages = max(1, -(-len(self.listed) // per_page))
        for n in range(1, pages + 1):
            chunk = self.listed[(n - 1) * per_page : n * per_page]
            pagination = {
                "current": n,
                "pages": pages,
                "prev": "/" if n == 2 else (f"/page/{n - 1}/" if n > 2 else None),
                "next": f"/page/{n + 1}/" if n < pages else None,
            }
            text = self.env.get_template("home.html").render(
                posts=chunk, summaries=summaries, pagination=pagination, gemini_path="/"
            )
            pb.add("index.html" if n == 1 else f"page/{n}/index.html", _tidy(text, "web"))

    def render_404(self, pb: PlatformBuild) -> None:
        text = self.env.get_template("404.html").render(gemini_path="/")
        pb.add("404.html", _tidy(text, "web"))

    def copy_media(self, posts: list[Post], pages: list[Page], pb: PlatformBuild) -> None:
        for item in [*posts, *pages]:
            for m in item.media:
                target = media_path(item.slug if item.kind == "post" else None, m.name).lstrip("/")
                pb.add(target, b"", media_type_for(m.name), source=m)
        site_media = self.site.root / "media"
        if site_media.is_dir():
            for f in sorted(site_media.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    rel = f.relative_to(site_media).as_posix()
                    pb.add(f"media/{rel}", b"", media_type_for(f.name), source=f)

    def copy_static(self, pb: PlatformBuild) -> None:
        for t in reversed(self.chain):
            static = t.static_dir()
            if not static.is_dir():
                continue
            for f in sorted(static.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(static).as_posix()
                    pb.add(f"assets/{t.name}/{rel}", b"", media_type_for(f.name), source=f)


def _date_filter(cfg: Any) -> Any:
    from constellation.core.content import site_tz

    tz = site_tz(cfg.site.timezone)

    def date(d: datetime, style: str = "long", calendar: str = "gregorian") -> str:
        d = d.astimezone(tz)
        if calendar != "gregorian":
            from constellation.core.calendars import format_date

            return format_date(d, style, calendar)
        if style == "iso":
            return d.strftime("%Y-%m-%d")
        if style == "short":
            return d.strftime("%Y-%m-%d")
        if style == "long":
            return f"{d.strftime('%B')} {d.day}, {d.year}"
        return d.strftime(style)

    return date


def _ddate(d: datetime, fmt: str, cfg: Any) -> str:
    from constellation.core.calendars import ddate
    from constellation.core.content import site_tz

    return ddate(d.astimezone(site_tz(cfg.site.timezone)), fmt)


def _tidy(text: str, platform: str) -> str:
    if platform == "gopher":
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        while lines and lines[-1].strip() == "":
            lines.pop()
        return "\r\n".join(lines) + "\r\n"
    if platform == "gemini":
        lines = [ln.rstrip() for ln in text.split("\n")]
        out: list[str] = []
        for ln in lines:
            if ln == "" and out and out[-1] == "":
                continue
            out.append(ln)
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n"
    return text if text.endswith("\n") else text + "\n"


def write_build(build: Build, out_dir: Path, *, clean: bool = True) -> None:
    out_dir = Path(out_dir)
    for name, pb in build.platforms.items():
        target = out_dir / name
        if clean and target.exists():
            shutil.rmtree(target)
        for path, f in pb.files.items():
            dest = target / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if f.source is not None:
                shutil.copyfile(f.source, dest)
            else:
                dest.write_bytes(f.data)
    (out_dir / "manifest.json").write_text(
        json.dumps(build.manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def diff_manifests(
    old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    added = sorted(p for p in new if p not in old)
    removed = sorted(p for p in old if p not in new)
    changed = sorted(p for p in new if p in old and old[p]["sha256"] != new[p]["sha256"])
    return {"added": added, "removed": removed, "changed": changed}
