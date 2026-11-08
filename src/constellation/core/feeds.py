"""Machine-readable outputs: Atom, JSON Feed, sitemap, robots.txt, favicon.txt."""

from __future__ import annotations

import json
import re
from datetime import datetime
from xml.sax.saxutils import escape

from constellation.core.models import Post, Site
from constellation.core.routes import absolute_url, platform_path


def _iso(d: datetime) -> str:
    return d.isoformat()


_REL_URL = re.compile(r'(src|href)="(/[^"]*)"')


def absolutize(html: str, base: str) -> str:
    """Feed readers show content out of context, so site-relative URLs become absolute."""
    if not base:
        return html
    return _REL_URL.sub(lambda m: f'{m.group(1)}="{base}{m.group(2)}"', html)


def atom_feed(
    site: Site,
    posts: list[Post],
    *,
    platform: str,
    now: datetime,
    bodies: dict[str, str],
    summaries: dict[str, str],
    feed_path: str = "/feed.xml",
    title: str | None = None,
    self_path: str | None = None,
) -> str:
    """An Atom feed. On the web the content is HTML; on Gemini it is the gemtext as text."""
    cfg = site.config
    base = cfg.platform(platform).base_url
    home = base + "/" if base else "/"
    feed_url = absolute_url(cfg, platform, self_path or feed_path)
    updated = max([p.updated or p.date for p in posts], default=now)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(title or cfg.site.title)}</title>",
    ]
    if cfg.site.tagline and not title:
        out.append(f"  <subtitle>{escape(cfg.site.tagline)}</subtitle>")
    out += [
        f'  <link href="{escape(home)}"/>',
        f'  <link rel="self" href="{escape(feed_url)}"/>',
        f"  <id>{escape(feed_url)}</id>",
        f"  <updated>{_iso(updated)}</updated>",
        "  <generator>ConstellationCMS</generator>",
    ]
    default_author = cfg.author_by_id(cfg.site.author) if cfg.site.author else None
    if default_author:
        out.append(f"  <author><name>{escape(default_author.name)}</name></author>")
    for p in posts:
        url = absolute_url(cfg, platform, platform_path(cfg, platform, "post", p.slug))
        author = cfg.author_by_id(p.meta.author) if p.meta.author else default_author
        out.append("  <entry>")
        out.append(f"    <title>{escape(p.meta.title)}</title>")
        out.append(f'    <link href="{escape(url)}"/>')
        out.append(f"    <id>{escape(url)}</id>")
        out.append(f"    <published>{_iso(p.date)}</published>")
        out.append(f"    <updated>{_iso(p.updated or p.date)}</updated>")
        if author:
            out.append(f"    <author><name>{escape(author.name)}</name></author>")
        for t in p.meta.tags:
            out.append(f'    <category term="{escape(t, {chr(34): "&quot;"})}"/>')
        summary = summaries.get(p.slug, "")
        if summary:
            out.append(f'    <summary type="text">{escape(summary)}</summary>')
        body = bodies.get(p.slug, "")
        if body:
            ctype = "html" if platform == "web" else "text"
            if platform == "web":
                body = absolutize(body, base)
            out.append(f'    <content type="{ctype}">{escape(body)}</content>')
        out.append("  </entry>")
    out.append("</feed>")
    return "\n".join(out) + "\n"


def json_feed(
    site: Site,
    posts: list[Post],
    *,
    now: datetime,
    bodies: dict[str, str],
    summaries: dict[str, str],
) -> str:
    cfg = site.config
    base = cfg.platform("web").base_url
    items = []
    for p in posts:
        url = absolute_url(cfg, "web", platform_path(cfg, "web", "post", p.slug))
        author = cfg.author_by_id(p.meta.author or cfg.site.author)
        item: dict[str, object] = {
            "id": url,
            "url": url,
            "title": p.meta.title,
            "content_html": absolutize(bodies.get(p.slug, ""), base),
            "date_published": _iso(p.date),
        }
        if p.updated:
            item["date_modified"] = _iso(p.updated)
        if summaries.get(p.slug):
            item["summary"] = summaries[p.slug]
        if p.meta.tags:
            item["tags"] = list(p.meta.tags)
        if author:
            item["authors"] = [{"name": author.name, **({"url": author.url} if author.url else {})}]
        items.append(item)
    feed: dict[str, object] = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": cfg.site.title,
        "home_page_url": base + "/" if base else "/",
        "feed_url": absolute_url(cfg, "web", "/feed.json"),
        "items": items,
    }
    if cfg.site.tagline:
        feed["description"] = cfg.site.tagline
    return json.dumps(feed, ensure_ascii=False, indent=2) + "\n"


def sitemap(site: Site, entries: list[tuple[str, datetime | None]]) -> str:
    """``entries`` are (web path, last modified) pairs."""
    cfg = site.config
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, mod in entries:
        out.append("  <url>")
        out.append(f"    <loc>{escape(absolute_url(cfg, 'web', path))}</loc>")
        if mod is not None:
            out.append(f"    <lastmod>{_iso(mod)}</lastmod>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def robots_web(site: Site) -> str:
    cfg = site.config
    lines = ["User-agent: *", "Allow: /", "Disallow: /_constellation/"]
    if cfg.platform("web").base_url:
        lines.append(f"Sitemap: {absolute_url(cfg, 'web', '/sitemap.xml')}")
    return "\n".join(lines) + "\n"


def robots_gemini() -> str:
    """Gemini's robots convention uses virtual user agents; archives and indexers are welcome."""
    return "User-agent: webproxy\nDisallow: /_constellation/\n\nUser-agent: *\nDisallow: /_constellation/\n"


def favicon_txt(site: Site) -> str:
    icon = str(site.config.theme.get("gemini_favicon", "") or "")
    return (icon or "✦") + "\n"
