"""URL and output-path conventions per platform, and link-target resolution."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

from constellation.core.markdown.document import Ref
from constellation.core.models import SiteConfig

EXTERNAL_SCHEMES = ("http", "https", "gemini", "gopher", "mailto", "xmpp", "finger", "spartan", "nex", "tel", "irc", "ftp")
PLATFORM_EXT = {"gemini": ".gmi", "gopher": ".txt"}


def web_path(cfg: SiteConfig, kind: str, slug: str, **extra: str) -> str:
    """The canonical web path for a content kind, from the site's URL patterns."""
    if kind == "home":
        return "/"
    pattern = getattr(cfg.urls, kind)
    assert isinstance(pattern, str)
    return pattern.format(slug=slug, **extra)


def platform_path(cfg: SiteConfig, platform: str, kind: str, slug: str, **extra: str) -> str:
    """The request path on a platform. Web keeps directory URLs; text platforms use files."""
    path = web_path(cfg, kind, slug, **extra)
    if platform == "web":
        return path
    ext = PLATFORM_EXT.get(platform, ".txt")
    if path == "/":
        return "/"
    return path.rstrip("/") + ext


def output_file(cfg: SiteConfig, platform: str, kind: str, slug: str, **extra: str) -> str:
    """The file written into the platform's output tree, relative, no leading slash."""
    path = platform_path(cfg, platform, kind, slug, **extra)
    if platform == "web":
        return path.lstrip("/") + "index.html" if path.endswith("/") else path.lstrip("/")
    if path == "/":
        return "index" + PLATFORM_EXT.get(platform, ".txt")
    return path.lstrip("/")


def media_path(item_slug: str | None, file: str) -> str:
    """Media lives at the same path on every platform."""
    if file.startswith("/"):
        return "/media/" + file.lstrip("/")
    if item_slug is None:
        return "/media/" + file
    return f"/media/posts/{item_slug}/{file}"


def url_for(ref: Ref, platform: str, cfg: SiteConfig, *, item_slug: str | None = None) -> str:
    """Resolve a ref to a site-relative URL (or the external URL as given)."""
    frag = f"#{ref.fragment}" if ref.fragment else ""
    if ref.kind == "external":
        return ref.target + frag
    if ref.kind == "fragment":
        return frag or "#"
    if ref.kind == "home":
        return "/" + frag
    if ref.kind == "media":
        return media_path(item_slug, ref.target) + frag
    return platform_path(cfg, platform, ref.kind, ref.target) + frag


def absolute_url(cfg: SiteConfig, platform: str, path: str) -> str:
    base = cfg.platform(platform).base_url
    return base + path if base else path


class RefResolver:
    """Turns link/image targets written by the author into :class:`Ref` objects.

    Canonical forms: ``post:slug``, ``page:slug``, ``tag:slug``, ``category:slug``,
    ``series:slug``, ``media:file`` (``media:/file`` for site-wide media), ``home:``.
    Also understood: ``[[slug]]`` wikilinks (handled by the parser), relative ``.md`` paths,
    site-absolute paths matching the URL patterns, and absolute URLs to the site's own hostnames.
    """

    def __init__(self, cfg: SiteConfig | None = None, *, post_slugs: set[str] | None = None,
                 page_slugs: set[str] | None = None) -> None:
        self.cfg = cfg
        self.post_slugs = post_slugs or set()
        self.page_slugs = page_slugs or set()
        self.hostnames: set[str] = set()
        if cfg is not None:
            for p in cfg.platforms.values():
                self.hostnames.update(h.lower() for h in p.hostnames)
                if p.base_url:
                    host = urlsplit(p.base_url).hostname
                    if host:
                        self.hostnames.add(host.lower())

    def resolve(self, href: str, *, is_image: bool = False) -> Ref:
        raw = href.strip()
        target, _, fragment = raw.partition("#")
        frag = fragment or None
        if not target and fragment:
            return Ref("fragment", "", frag, raw)
        prefix, sep, rest = target.partition(":")
        if sep and prefix in ("post", "page", "tag", "category", "series"):
            return Ref(prefix, rest.strip("/"), frag, raw)  # type: ignore[arg-type]
        if sep and prefix == "media":
            return Ref("media", rest, frag, raw)
        if sep and prefix == "home":
            return Ref("home", "", frag, raw)
        parts = urlsplit(target)
        if parts.scheme:
            if parts.scheme in EXTERNAL_SCHEMES and parts.hostname and parts.hostname.lower() in self.hostnames:
                inner = self._site_path(parts.path)
                if inner is not None:
                    return inner.with_fragment(frag)
            return Ref("external", target, frag, raw)
        if target.startswith("/"):
            inner = self._site_path(target)
            if inner is not None:
                return inner.with_fragment(frag)
            return Ref("external", target, frag, raw)
        # Relative: a .md file next to this one, or a bundle folder, or a media file.
        if target.endswith(".md"):
            stem = posixpath.basename(target)[:-3]
            stem = stem if stem != "index" else posixpath.basename(posixpath.dirname(target))
            slug = _strip_date(stem)
            if slug in self.page_slugs and slug not in self.post_slugs:
                return Ref("page", slug, frag, raw)
            return Ref("post", slug, frag, raw)
        if is_image or "." in posixpath.basename(target):
            return Ref("media", target, frag, raw)
        return Ref("post", _strip_date(target.strip("/")), frag, raw)

    def _site_path(self, path: str) -> Ref | None:
        if self.cfg is None:
            return None
        path = path or "/"
        if path == "/":
            return Ref("home", "", None, path)
        if path.startswith("/media/"):
            rest = path[len("/media/"):]
            if rest.startswith("posts/"):
                _, _, file = rest.partition("/")
                _slug, _, file = file.partition("/")
                return Ref("media", file, None, path)
            return Ref("media", "/" + rest, None, path)
        for kind in ("post", "page", "tag", "category", "series"):
            pattern = getattr(self.cfg.urls, kind)
            rx = "^" + re.escape(pattern).replace(r"\{slug\}", r"(?P<slug>[^/]+)") + "$"
            candidates = [path if path.endswith("/") else path + "/"]
            for ext in PLATFORM_EXT.values():
                if path.endswith(ext):
                    candidates.insert(0, path[: -len(ext)] + "/")
            for candidate in candidates:
                m = re.match(rx, candidate)
                if m:
                    return Ref(kind, m.group("slug"), None, path)
        return None


_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _strip_date(name: str) -> str:
    return _DATE_PREFIX.sub("", name)
