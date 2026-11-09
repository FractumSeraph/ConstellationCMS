"""Import a site built by Publii into a ConstellationCMS site folder.

The input is the folder Publii publishes (what it uploads to the web host, or a copy such as a
GitHub Pages repository). Publii itself isn't needed. From each page it reads what Publii always
writes: the body class (``post-template``, ``page-template``), the schema.org ``Article`` data
(title, dates, description, author), and the post body; from ``feed.json`` the tags and which
summaries were written by hand. Addresses are kept as they were, so old links keep working.
"""

from __future__ import annotations

import colorsys
import html
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import tomlkit
from slugify import slugify

from constellation.core import umami
from constellation.importers.html_to_md import html_to_markdown

CONTENT_CLASSES = (
    "post__entry",
    "post__content",
    "content__entry",
    "entry-content",
    "post-content",
    "article__content",
)
# Parts of a post body that are Publii furniture, not the writing.
FURNITURE_CLASSES = ("post__toc", "post__share", "post__related", "post__comments", "post__bio")


class PubliiImportError(ValueError):
    """The source isn't a Publii site, or the destination can't be written."""


@dataclass
class ImportReport:
    site_dir: Path
    posts: int = 0
    pages: int = 0
    tags: int = 0
    authors: int = 0
    media: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Item:
    kind: str  # post | page
    path: str  # "/slug/" as on the old site
    slug: str
    title: str
    body: str
    date: str = ""
    updated: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    image: str = ""
    image_alt: str = ""
    media: dict[str, Path] = field(default_factory=dict)  # file name in bundle -> source file


# ---- small HTML helpers ----------------------------------------------------------------------------
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _element(doc: str, start: int, tag: str) -> tuple[int, int, int]:
    """For the start tag at ``start``: (start, end of start tag, end of the element)."""
    open_end = doc.index(">", start) + 1
    depth, pos = 1, open_end
    rx = re.compile(rf"<(/?){tag}\b[^>]*>", re.I)
    while depth:
        m = rx.search(doc, pos)
        if m is None:
            return start, open_end, len(doc)
        depth += -1 if m.group(1) else 1
        pos = m.end()
    return start, open_end, pos


def _find_by_class(
    doc: str, cls: str, within: tuple[int, int] | None = None
) -> tuple[int, int, int] | None:
    lo, hi = within or (0, len(doc))
    rx = re.compile(r"<(\w+)\b[^>]*\bclass=\"([^\"]*)\"[^>]*>", re.I)
    for m in rx.finditer(doc, lo, hi):
        if cls in m.group(2).split():
            return _element(doc, m.start(), m.group(1))
    return None


def _inner(doc: str, cls: str) -> str:
    found = _find_by_class(doc, cls)
    if found is None:
        return ""
    _s, a, e = found
    close = doc.rfind("</", a, e)
    return doc[a:close]


def _remove_classes(fragment: str, classes: tuple[str, ...]) -> str:
    for cls in classes:
        while (found := _find_by_class(fragment, cls)) is not None:
            s, _a, e = found
            fragment = fragment[:s] + fragment[e:]
    return fragment


def _attr(tag_html: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*(\"([^\"]*)\"|'([^']*)')", tag_html, re.I)
    return html.unescape(
        m.group(2) if m and m.group(2) is not None else (m.group(3) if m else "") or ""
    )


def _ld_article(doc: str) -> dict[str, Any]:
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', doc, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") in ("Article", "BlogPosting"):
            return data
    return {}


def _iso(value: str) -> str:
    """Publii's "2025-10-21T18:56-05:00" as a full ISO date with seconds."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(.*)$", value.strip())
    if not m:
        return value.strip()
    return f"{m.group(1)}:00{m.group(2)}"


def _hsl_hex(triplet: str) -> str | None:
    m = re.match(r"\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%", triplet)
    if not m:
        return None
    h, s, lum = float(m.group(1)) / 360, float(m.group(2)) / 100, float(m.group(3)) / 100
    r, g, b = colorsys.hls_to_rgb(h, lum, s)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def _front_matter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if value in (None, "", [], {}):
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join([*lines, "---", ""]) + "\n"


# ---- the importer ----------------------------------------------------------------------------------
class _Importer:
    def __init__(self, src: Path, dest: Path, theme: str, timezone: str) -> None:
        self.src, self.dest, self.theme, self.timezone = src, dest, theme, timezone
        self.report = ImportReport(site_dir=dest)
        self.home = _read(src / "index.html")
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', self.home)
        self.base = (canonical.group(1) if canonical else "").rstrip("/")
        self.host = (urlsplit(self.base).hostname or "").lower()
        self.feed = self._feed()
        self.items: list[_Item] = []
        self.site_media: dict[str, Path] = {}  # path under media/ -> source file
        self.authors: dict[str, dict[str, str]] = {}
        self.tag_names: dict[str, str] = {}  # slug -> name
        self.tag_desc: dict[str, str] = {}  # slug -> description

    # -- addresses -----------------------------------------------------------------------------------
    def _site_path(self, url: str) -> str | None:
        """The path of a link to this site, or None for other sites."""
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and (parts.hostname or "").lower() in (
            self.host,
            "www." + self.host,
        ):
            return unquote(parts.path) or "/"
        if not parts.scheme and url.startswith("/"):
            return unquote(parts.path)
        return None

    def _source_file(self, path: str) -> Path | None:
        candidate = (self.src / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.src.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _rewrite(self, item: _Item) -> Any:
        def url(href: str, is_image: bool) -> str:
            path = self._site_path(href)
            if path is None:
                return href
            frag = "#" + urlsplit(href).fragment if urlsplit(href).fragment else ""
            if path.startswith("/media/"):
                source = self._source_file(path)
                if source is None:
                    self.report.warnings.append(f"{item.path}: missing file {path}")
                    return path
                if path.startswith("/media/posts/") and "/responsive/" not in path:
                    name = source.name
                    item.media[name] = source
                    return name
                rel = path[len("/media/") :]
                self.site_media[rel] = source
                return path
            return path + frag

        return url

    # -- reading -------------------------------------------------------------------------------------
    def _feed(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        f = self.src / "feed.json"
        if f.is_file():
            try:
                data = json.loads(_read(f))
                for entry in data.get("items", []):
                    path = urlsplit(str(entry.get("url", ""))).path
                    out[path] = entry
            except ValueError:
                self.report.warnings.append(
                    "feed.json could not be read; tags come from the pages instead"
                )
        return out

    def read_items(self) -> None:
        for page in sorted(self.src.rglob("index.html")):
            rel = page.parent.relative_to(self.src).as_posix()
            if rel == "." or rel.split("/")[0] in ("assets", "media", "tags", "authors", "page"):
                continue
            if "/page/" in f"/{rel}/":
                continue
            doc = _read(page)
            body_class = re.search(r'<body[^>]*class="([^"]*)"', doc)
            classes = body_class.group(1).split() if body_class else []
            kind = (
                "post"
                if "post-template" in classes
                else "page"
                if "page-template" in classes
                else ""
            )
            if not kind:
                continue
            self.items.append(self._read_item(kind, "/" + rel + "/", doc))

    def _read_item(self, kind: str, path: str, doc: str) -> _Item:
        ld = _ld_article(doc)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.S)
        title = str(ld.get("headline") or (_text(h1.group(1)) if h1 else path.strip("/")))
        item = _Item(
            kind=kind,
            path=path,
            slug=path.rstrip("/").rsplit("/", 1)[-1],
            title=html.unescape(title),
            body="",
        )
        item.date = _iso(str(ld.get("datePublished", "")))
        modified = _iso(str(ld.get("dateModified", "")))
        item.updated = modified if modified and modified[:10] != item.date[:10] else ""
        item.description = html.unescape(str(ld.get("description", ""))).strip()
        author = ld.get("author") or {}
        if isinstance(author, dict):
            a_path = self._site_path(str(author.get("url", ""))) or ""
            m = re.match(r"^/authors/([^/]+)/", a_path)
            a_id = m.group(1) if m else slugify(str(author.get("name", "")))
            if a_id:
                item.author = a_id
                self.authors.setdefault(a_id, {"name": str(author.get("name", "")) or a_id})
        # Tags: the links in the post header, in the order shown; the feed when there are none.
        feed = self.feed.get(path, {})
        tag_links = re.findall(
            r'<a href="([^"]*/tags/([^/"]+)/)"[^>]*>(.*?)</a>',
            doc.split("<main", 1)[-1].split("</header>", 1)[0],
        )
        for _href, t_slug, t_name in tag_links:
            self.tag_names.setdefault(t_slug, _text(t_name).lstrip("#"))
        item.tags = [_text(n).lstrip("#") for _h, _s, n in tag_links] or [
            str(t) for t in feed.get("tags", [])
        ]
        item.tags = list(dict.fromkeys(t for t in item.tags if t))
        # Hand-written summaries: the feed's summary when it doesn't end in an ellipsis.
        fs = _text(str(feed.get("summary", "")))
        if fs and not fs.endswith(("…", "...")):
            item.summary = fs
        # Body and featured image.
        content = ""
        for cls in CONTENT_CLASSES:
            content = _inner(doc, cls)
            if content:
                break
        if not content:
            m = re.search(r"<article\b[^>]*>(.*)</article>", doc, re.S)
            content = m.group(1) if m else ""
            content = re.sub(r"<header\b.*?</header>", "", content, count=1, flags=re.S)
            self.report.warnings.append(f"{path}: body found by fallback; check it")
        content = _remove_classes(content, FURNITURE_CLASSES)
        rewrite = self._rewrite(item)
        header = doc.split("<main", 1)[-1].split("</header>", 1)[0] if "<main" in doc else ""
        fig = re.search(r"<img\b[^>]*>", header)
        if fig and ("featured" in header or "hero__image" in header or "post__image" in header):
            item.image = rewrite(_attr(fig.group(0), "src"), True)
            item.image_alt = _attr(fig.group(0), "alt")
        item.body = html_to_markdown(content, rewrite)
        if "<iframe" in item.body or "<video" in item.body or "<form" in item.body:
            self.report.warnings.append(
                f"{path}: kept embedded HTML (video, iframe or form); it shows on the web only"
            )
        return item

    def read_listing_summaries(self) -> None:
        """Hand-written summaries for posts the feed doesn't list (it holds only the newest)."""
        by_path = {i.path: i for i in self.items if i.kind == "post"}
        for listing in [self.src / "index.html", *sorted(self.src.glob("page/*/index.html"))]:
            doc = _read(listing)
            for chunk in re.split(r"<article\b", doc)[1:]:
                link = re.search(r'<a href="([^"]+)"', chunk)
                path = self._site_path(link.group(1)) if link else None
                item = by_path.get(path or "")
                if item is None or path in self.feed:
                    continue
                excerpt = re.search(r"<p>(.*?)</p>", chunk, re.S)
                text = _text(excerpt.group(1)) if excerpt else ""
                if text and not text.endswith(("…", "...")):
                    item.summary = text

    def summary_words(self) -> int:
        """Publii cuts automatic summaries at a word count; find it from those that were cut."""
        counts: Counter[int] = Counter()
        for entry in self.feed.values():
            text = _text(str(entry.get("summary", "")))
            if text.endswith(("…", "...")):
                counts[len(text.rstrip("….").split())] += 1
        return counts.most_common(1)[0][0] if counts else 0

    def read_tags_and_authors(self) -> None:
        for page in sorted(self.src.glob("tags/*/index.html")):
            doc = _read(page)
            desc = _inner(doc, "page__desc") or _inner(doc, "tag__desc")
            name = self.tag_names.get(page.parent.name, "")
            if desc:
                self.tag_names.setdefault(page.parent.name, name)
                self.tag_desc[page.parent.name] = _text(desc)
        for page in sorted(self.src.glob("authors/*/index.html")):
            a_id = page.parent.name
            doc = _read(page)
            info = self.authors.setdefault(a_id, {"name": a_id})
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.S)
            if h1:
                info["name"] = (
                    re.sub(
                        r"\s*\(\d+ Posts?\)\s*$",
                        "",
                        _text(re.sub(r"<sup.*?</sup>", "", h1.group(1), flags=re.S)),
                    )
                    or info["name"]
                )
            img = re.search(r'<img\b[^>]*class="[^"]*avatar[^"]*"[^>]*>', doc)
            if img:
                path = self._site_path(_attr(img.group(0), "src"))
                source = self._source_file(path) if path else None
                if source is not None:
                    self.site_media[f"authors/{a_id}{source.suffix}"] = source
                    info["avatar"] = f"/authors/{a_id}{source.suffix}"
            bio = (
                _inner(doc, "page--author__desc")
                or _inner(doc, "author__desc")
                or _inner(doc, "author__bio")
            )
            if bio:
                info["bio"] = _text(bio)
            site = re.search(
                r'class="[^"]*website[^"]*"[^>]*>\s*<a href="([^"]+)"', doc
            ) or re.search(r'<a href="([^"]+)"[^>]*class="[^"]*website', doc)
            if site:
                info["url"] = site.group(1)

    def read_menu(self) -> list[dict[str, Any]]:
        header = self.home.split("<header", 1)[-1].split("</header>", 1)[0]
        nav = re.search(r"<nav\b.*?</nav>", header, re.S)
        if not nav:
            return []
        top = re.search(r"<ul\b[^>]*>", nav.group(0))
        if not top:
            return []
        frag = nav.group(0)
        _s, a, e = _element(frag, top.start(), "ul")
        return self._menu_items(frag[a : frag.rfind("</ul>", a, e)])

    def _menu_items(self, ul_inner: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        pos = 0
        rx = re.compile(r"<li\b[^>]*>", re.I)
        while (m := rx.search(ul_inner, pos)) is not None:
            _s, a, e = _element(ul_inner, m.start(), "li")
            li = ul_inner[a : ul_inner.rfind("</li>", a, e)]
            pos = e
            sub = re.search(r"<ul\b[^>]*>", li)
            head = li[: sub.start()] if sub else li
            link = re.search(r'<a href="([^"]*)"[^>]*>(.*?)</a>', head, re.S)
            label = _text(link.group(2)) if link else _text(head)
            entry: dict[str, Any] = {"label": label}
            if link:
                entry.update(self._menu_target(link.group(1)))
            if sub:
                _ss, sa, se = _element(li, sub.start(), "ul")
                entry["children"] = self._menu_items(li[sa : li.rfind("</ul>", sa, se)])
            items.append(entry)
        return items

    def _menu_target(self, href: str) -> dict[str, str]:
        path = self._site_path(href)
        if path is None:
            return {"url": href}
        if path in ("", "/"):
            return {"ref": "home"}
        for item in self.items:
            if item.path == path:
                return {"ref": f"{item.kind}:{item.slug}"}
        m = re.match(r"^/tags/([^/]+)/$", path)
        if m:
            return {"ref": f"tag:{m.group(1)}"}
        m = re.match(r"^/authors/([^/]+)/$", path)
        if m:
            return {"ref": f"author:{m.group(1)}"}
        return {"url": path}

    def read_custom_code(self) -> tuple[str, str, umami.UmamiCode | None]:
        """The site's own code from Publii's "Custom code" settings, minus Publii's own tags."""
        head = self.home.split("<head>", 1)[-1].split("</head>", 1)[0]
        found: umami.UmamiCode | None = None
        kept: list[str] = []
        seen: set[str] = set()

        def keep(block: str) -> None:
            nonlocal found
            key = re.sub(r"\s+", " ", block).strip()
            if key in seen:
                return
            seen.add(key)
            if "data-website-id" in block:
                try:
                    found = found or umami.parse_tracking_code(block)
                    return
                except umami.UmamiCodeError:
                    pass
            kept.append(block.strip())

        blocks: list[tuple[int, str]] = []
        for m in re.finditer(r"<(script|noscript|style)\b[^>]*>.*?</\1>", head, re.S | re.I):
            block = m.group(0)
            if (
                'type="application/ld+json"' in block
                or "img[loading]" in block  # Publii's lazy-loading fallback
                or "--body-font" in block
                or "--page-width" in block
            ):
                continue
            blocks.append((m.start(), block))
        for m in re.finditer(r"<(div|aside|a)\b[^>]*>", head, re.I):
            if any(s <= m.start() < s + len(b) for s, b in blocks):
                continue
            s, _a, e = _element(head, m.start(), m.group(1))
            blocks.append((s, head[s:e]))
        for _pos, block in sorted(blocks):
            keep(block)
        head_code = "\n".join(kept)
        # Code Publii puts before </body>: everything after the footer that isn't Publii's own.
        tail = self.home.rsplit("</footer>", 1)[-1]
        body_kept: list[str] = []
        for m in re.finditer(r"<(script|noscript)\b[^>]*>.*?</\1>", tail, re.S | re.I):
            block = m.group(0)
            if "/assets/js/" in block or "publiiTheme" in block or "is-loaded" in block:
                continue
            before = len(kept)
            keep(block)
            if len(kept) > before:
                body_kept.append(kept.pop())
        return head_code, "\n".join(body_kept), found

    def theme_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        hero = _inner(self.home, "hero")
        if hero:
            settings["welcome"] = _text(hero)
        css = self.src / "assets" / "css" / "style.css"
        if self.theme == "fractum" and css.is_file():
            root = re.search(r":root\{([^}]*)\}", _read(css))
            if root:
                vars_ = dict(re.findall(r"--([\w-]+):([^;]+)", root.group(1)))
                mapping = {"accent": "accent", "background": "background", "color": "foreground"}
                defaults = {"accent": "#de7aeb", "background": "#222129", "foreground": "#ffffff"}
                for css_name, key in mapping.items():
                    hexed = _hsl_hex(vars_.get(css_name, ""))
                    if hexed and abs(int(hexed[1:], 16) - int(defaults[key][1:], 16)) > 0x030303:
                        settings[key] = hexed
        return settings

    # -- writing -------------------------------------------------------------------------------------
    def write(self) -> None:
        dest = self.dest
        (dest / "content" / "posts").mkdir(parents=True, exist_ok=True)
        (dest / "content" / "pages").mkdir(parents=True, exist_ok=True)
        (dest / "media").mkdir(exist_ok=True)
        authors_count = Counter(i.author for i in self.items if i.kind == "post" and i.author)
        default_author = authors_count.most_common(1)[0][0] if authors_count else ""
        for item in self.items:
            front: dict[str, Any] = {"title": item.title, "slug": item.slug}
            if item.kind == "post":
                front["date"] = item.date
            if item.updated:
                front["updated"] = item.updated
            if item.author and item.author != default_author:
                front["author"] = item.author
            if item.kind == "post":
                front["tags"] = item.tags
            front["summary"] = item.summary
            if item.description and item.description != item.summary:
                front["seo"] = {"description": item.description}
            front["image"], front["image_alt"] = item.image, item.image_alt
            text = _front_matter(front) + item.body
            if item.kind == "post":
                folder = dest / "content" / "posts" / f"{item.date[:10]}-{item.slug}"
            else:
                folder = dest / "content" / "pages" / item.slug
            if item.media or item.kind == "post":
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "index.md").write_text(text, encoding="utf-8", newline="\n")
                for name, source in item.media.items():
                    shutil.copy2(source, folder / name)
                    self.report.media += 1
            else:
                folder.with_suffix(".md").write_text(text, encoding="utf-8", newline="\n")
            if item.kind == "post":
                self.report.posts += 1
            else:
                self.report.pages += 1
        tag_desc = self.tag_desc
        used_tags = {t for i in self.items for t in i.tags}
        for t_slug, desc in tag_desc.items():
            name = self.tag_names.get(t_slug, "")
            if not desc:
                continue
            (dest / "content" / "tags").mkdir(parents=True, exist_ok=True)
            (dest / "content" / "tags" / f"{t_slug}.md").write_text(
                _front_matter({"title": name, "description": desc}), encoding="utf-8", newline="\n"
            )
        self.report.tags = len(used_tags)
        for rel, source in self.site_media.items():
            target = dest / "media" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            self.report.media += 1
        icon = re.search(r'<link rel="(?:shortcut )?icon" href="([^"]+)"', self.home)
        if icon:
            icon_path = self._site_path(icon.group(1))
            icon_file = self._source_file(icon_path) if icon_path else None
            if icon_file is not None:
                shutil.copy2(icon_file, dest / "media" / f"favicon{icon_file.suffix.lower()}")
        self._write_site_toml(default_author)

    def _write_site_toml(self, default_author: str) -> None:
        home = self.home
        title_el = _inner(home, "logo")
        og = re.search(r'<meta property="og:site_name" content="([^"]*)"', home)
        title_tag = re.search(r"<title>(.*?)</title>", home, re.S)
        title = (
            _text(title_el)
            or (html.unescape(og.group(1)) if og else "")
            or (_text(title_tag.group(1)) if title_tag else "Site")
        )
        desc = re.search(r'<meta name="description" content="([^"]*)"', home)
        lang = re.search(r'<html[^>]*lang="([^"]+)"', home)
        footer = re.search(r"<footer\b.*?</footer>", home, re.S)
        doc = tomlkit.document()
        doc["version"] = 1
        site = tomlkit.table()
        site["title"] = title
        site["tagline"] = ""
        site["language"] = (lang.group(1) if lang else "en").split("-")[0]
        site["timezone"] = self.timezone
        site["theme"] = self.theme
        site["author"] = default_author
        doc["site"] = site
        if self.authors:
            aot = tomlkit.aot()
            for a_id, info in self.authors.items():
                t = tomlkit.table()
                t["id"] = a_id
                for key in ("name", "bio", "url", "avatar"):
                    if info.get(key):
                        t[key] = info[key]
                aot.append(t)
            doc["authors"] = aot
        web = tomlkit.table()
        web["enabled"] = True
        web["base_url"] = self.base
        web["hostnames"] = [self.host] if self.host else []
        per_page = len(re.findall(r"<article\b", home.split("<main", 1)[-1]))
        web["pagination"] = per_page or 10
        web["feeds"] = ["atom", "json"]
        platforms = tomlkit.table()
        platforms["web"] = web
        doc["platforms"] = platforms
        post_prefixes = {
            i.path.rstrip("/").rsplit("/", 1)[0] for i in self.items if i.kind == "post"
        }
        if len(post_prefixes) == 1:
            urls = tomlkit.table()
            urls["post"] = post_prefixes.pop() + "/{slug}/"
            doc["urls"] = urls
        else:
            self.report.warnings.append(
                "posts live under different folders; kept the default /posts/{slug}/"
            )
        menu = tomlkit.table()
        menu["items"] = self.read_menu() or [{"label": "Home", "ref": "home"}]
        doc["menu"] = menu
        doc["footer"] = {"text": _text(footer.group(0)) if footer else ""}
        doc["seo"] = {"default_description": html.unescape(desc.group(1)) if desc else ""}
        head_code, body_code, um = self.read_custom_code()
        custom = tomlkit.table()
        custom["head"] = (
            tomlkit.string(head_code, multiline=True) if "\n" in head_code else head_code
        )
        custom["body_start"] = ""
        custom["body_end"] = (
            tomlkit.string(body_code, multiline=True) if "\n" in body_code else body_code
        )
        doc["custom_html"] = custom
        words = self.summary_words()
        if words:
            render = tomlkit.table()
            render["summary_words"] = words
            doc["render"] = render
        if um is not None:
            analytics = tomlkit.table()
            table = tomlkit.table()
            table["script_url"] = um.script_url
            table["website_id"] = um.website_id
            if um.host_url:
                table["host_url"] = um.host_url
            analytics["umami"] = table
            doc["analytics"] = analytics
        redirects = []
        for kind in ("authors", "tags"):
            for paged in sorted(self.src.glob(f"{kind}/*/page/*/index.html")):
                rel = paged.parent.relative_to(self.src).as_posix()
                base = "/" + rel.split("/page/")[0] + "/"
                redirects.append({"from": f"/{rel}/", "to": base, "status": 301})
        if redirects:
            doc["redirects"] = redirects
        settings = self.theme_settings()
        if settings:
            theme = tomlkit.table()
            theme["settings"] = settings
            doc["theme"] = theme
        self.report.authors = len(self.authors)
        (self.dest / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")


def looks_like_publii(src: Path) -> bool:
    index = Path(src) / "index.html"
    return index.is_file() and 'name="generator" content="Publii' in _read(index)


def import_publii(
    src: Path, dest: Path, *, theme: str = "fractum", timezone: str = "UTC"
) -> ImportReport:
    """Read the built Publii site at ``src`` and write a new site folder at ``dest``."""
    src, dest = Path(src), Path(dest)
    if not looks_like_publii(src):
        raise PubliiImportError(
            f"{src} doesn't look like a site built by Publii (no index.html made by Publii). "
            "Point at the folder Publii publishes, the one with index.html, assets/ and media/."
        )
    if dest.exists() and any(dest.iterdir()):
        raise PubliiImportError(f"{dest} already has files in it; choose a new or empty folder.")
    importer = _Importer(src, dest, theme, timezone)
    importer.read_items()
    if not importer.items:
        raise PubliiImportError(f"No posts or pages found in {src}.")
    importer.read_listing_summaries()
    importer.read_tags_and_authors()
    importer.write()
    return importer.report
