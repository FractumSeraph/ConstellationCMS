"""Reader-facing extras worked out at build time: table of contents, related posts, featured
posts, share links, comments and the search index.

Templates receive plain data from here; the markup lives in the base theme's partials
(``partials/toc.html`` and friends) so any theme can include or restyle them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from constellation.core.markdown.document import Document
from constellation.core.models import Page, Post, SiteConfig
from constellation.core.render.plaintext import plain_text

ASSET_PREFIX = "assets/constellation/"
SEARCH_PATH = "search/index.html"

SHARE_LABELS = {
    "mastodon": "Mastodon",
    "bluesky": "Bluesky",
    "facebook": "Facebook",
    "x": "X",
    "reddit": "Reddit",
    "linkedin": "LinkedIn",
    "hackernews": "Hacker News",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "email": "Email",
    "copy": "Copy link",
}

# The status id is the last path part: https://host/@user/123 or https://host/users/u/statuses/123
MASTODON_TOOT = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?/(?:@[A-Za-z0-9_.@-]+|users/[A-Za-z0-9_.-]+/statuses|web/statuses)/(?P<id>\d{1,30})/?$"
)


@dataclass(slots=True)
class TocEntry:
    level: int
    text: str
    id: str


def toc_entries(
    headings: list[tuple[int, str, str]], cfg: SiteConfig, wanted: bool | None
) -> list[TocEntry]:
    """Headings for a table of contents, or [] when it should not be shown.

    ``wanted`` is the item's ``toc`` front matter: True always shows one (when there are any
    headings), False never does, and None follows ``[features]`` toc and toc_min_headings.
    """
    if wanted is False or not headings:
        return []
    if wanted is None and (not cfg.features.toc or len(headings) < cfg.features.toc_min_headings):
        return []
    top = min(level for level, _, _ in headings)
    return [TocEntry(level - top + 1, text, hid) for level, text, hid in headings if text]


def related_posts(post: Post, listed: list[Post], count: int) -> list[Post]:
    """Up to ``count`` listed posts sharing the most tags (then categories), newest first."""
    if count <= 0:
        return []
    tags = {t.lower() for t in post.meta.tags}
    cats = {c.lower() for c in post.meta.categories}
    if not tags and not cats:
        return []
    scored: list[tuple[int, Post]] = []
    for other in listed:
        if other.slug == post.slug:
            continue
        score = 2 * len(tags & {t.lower() for t in other.meta.tags}) + len(
            cats & {c.lower() for c in other.meta.categories}
        )
        if score:
            scored.append((score, other))
    scored.sort(key=lambda sp: (sp[0], sp[1].date, sp[1].slug), reverse=True)
    return [p for _, p in scored[:count]]


def pin_featured(posts: list[Post], cfg: SiteConfig) -> list[Post]:
    """Featured posts first (newest first among them), then everything else in order."""
    if not cfg.features.pin_featured:
        return posts
    featured = [p for p in posts if p.meta.featured]
    return featured + [p for p in posts if not p.meta.featured] if featured else posts


def share_links(networks: list[str], url: str, title: str) -> list[dict[str, str]]:
    """Plain links to each network's own share page. No scripts, so nothing tracks readers."""
    if not url:
        return []
    u, t = quote(url, safe=""), quote(title, safe="")
    both = quote(f"{title} {url}", safe="")
    targets = {
        "mastodon": f"https://s2f.kytta.dev/?text={both}",
        "bluesky": f"https://bsky.app/intent/compose?text={both}",
        "facebook": f"https://www.facebook.com/sharer/sharer.php?u={u}",
        "x": f"https://x.com/intent/post?text={t}&url={u}",
        "reddit": f"https://www.reddit.com/submit?url={u}&title={t}",
        "linkedin": f"https://www.linkedin.com/sharing/share-offsite/?url={u}",
        "hackernews": f"https://news.ycombinator.com/submitlink?u={u}&t={t}",
        "telegram": f"https://t.me/share/url?url={u}&text={t}",
        "whatsapp": f"https://wa.me/?text={both}",
        "email": f"mailto:?subject={t}&body={u}",
        "copy": url,
    }
    return [
        {"network": n, "label": SHARE_LABELS[n], "url": targets[n]}
        for n in networks
        if n in targets
    ]


def mastodon_toot(url: str) -> tuple[str, str] | None:
    """(host, status id) of a toot address, or None when it isn't one."""
    m = MASTODON_TOOT.match(url.strip())
    if not m:
        return None
    netloc = urlsplit(url.strip()).netloc
    return netloc, m.group("id")


def comments_context(
    cfg: SiteConfig, post: Post, page_url: str, path: str
) -> dict[str, Any] | None:
    """What ``partials/comments.html`` needs, or None when this post shows no comments."""
    c = cfg.comments
    if not c.provider or not post.meta.comments:
        return None
    if c.provider == "giscus":
        if not (c.repo and c.repo_id and c.category_id):
            return None
        return {
            "provider": "giscus",
            "repo": c.repo,
            "repo_id": c.repo_id,
            "category": c.category,
            "category_id": c.category_id,
            "mapping": c.mapping or "pathname",
            "theme": c.giscus_theme or "preferred_color_scheme",
            "lang": cfg.site.language or "en",
        }
    if c.provider == "disqus":
        if not c.shortname:
            return None
        return {
            "provider": "disqus",
            "shortname": c.shortname,
            "url": page_url,
            "identifier": path,
        }
    if c.provider == "mastodon":
        toot = mastodon_toot(post.meta.mastodon) if post.meta.mastodon else None
        if toot is None:
            return None
        return {
            "provider": "mastodon",
            "toot": post.meta.mastodon.strip(),
            "host": toot[0],
            "id": toot[1],
        }
    if c.provider == "custom":
        return {"provider": "custom", "html": c.html} if c.html.strip() else None
    return None


def search_index(
    entries: list[tuple[Post | Page, str, Document, str]], *, text_chars: int = 6000
) -> str:
    """The JSON the search page loads: title, address, date, summary, tags and body text.

    ``entries`` are (item, address, parsed document, summary). Body text is cut at
    ``text_chars`` characters so a big site still loads quickly.
    """
    rows = []
    for item, url, doc, summary in entries:
        text = plain_text(doc, max_chars=text_chars)
        rows.append(
            {
                "t": item.meta.title,
                "u": url,
                "d": item.date.date().isoformat() if item.kind == "post" else "",
                "s": summary,
                "g": list(getattr(item.meta, "tags", [])),
                "x": text,
            }
        )
    return json.dumps({"version": 1, "items": rows}, ensure_ascii=False, separators=(",", ":"))
