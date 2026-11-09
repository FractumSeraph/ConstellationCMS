"""`constellation import publii`: a small site in Publii's published layout, imported and built."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomlkit

from constellation.cli import main
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.importers.html_to_md import html_to_markdown
from constellation.importers.publii import PubliiImportError, import_publii, looks_like_publii

NOW = datetime(2026, 12, 1, tzinfo=UTC)
BASE = "https://example.org"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
WID = "11111111-2222-3333-4444-555555555555"


def _page(
    body_class: str,
    main: str,
    *,
    ld: dict[str, object] | None = None,
    head: str = "",
    tail: str = "",
) -> str:
    ld_tag = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld else ""
    return f"""<!DOCTYPE html><html lang="en-us"><head><meta charset="utf-8">
<title>Lantern Club</title><meta name="description" content="A club for lantern makers.">
<meta name="generator" content="Publii Open-Source CMS for Static Site">
<link rel="canonical" href="{BASE}/"><link rel="shortcut icon" href="{BASE}/media/website/icon.png" type="image/png">
<link rel="stylesheet" href="{BASE}/assets/css/style.css">{ld_tag}
<noscript><style>img[loading] {{ opacity: 1; }}</style></noscript>
{head}</head><body class="{body_class}"><div class="container"><header class="header">
<a class="logo" href="{BASE}/">Lantern Club</a>
<nav class="navbar"><ul class="navbar__menu"><li><a href="{BASE}/">Home</a></li>
<li class="has-submenu"><span class="is-separator">About us</span><ul class="navbar__submenu">
<li><a href="{BASE}/about/">About</a></li><li><a href="https://elsewhere.example/">Friends</a></li></ul></li>
<li><a href="{BASE}/tags/glass/">Glass</a></li></ul></nav></header>
<main class="content">{main}</main><footer class="footer"><p>Lantern Club</p></footer></div>
<script defer src="{BASE}/assets/js/scripts.min.js"></script><script>window.publiiThemeMenuConfig={{}};</script>
{tail}</body></html>"""


HEAD = f"""<script async src="https://stats.example/gtag.js"></script>
<script defer src="https://umami.example/script.js" data-website-id="{WID}"></script>
<div id="chat-button"><a href="https://chat.example/">Chat</a></div>"""


def _write(root: Path, rel: str, text: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def _article(slug: str, title: str, tags: list[tuple[str, str]], excerpt: str) -> str:
    tag_links = " ".join(f'<a href="{BASE}/tags/{s}/" class="invert">{n}</a>' for s, n in tags)
    return f"""<article class="post"><header><h2 class="post__title"><a href="{BASE}/{slug}/">{title}</a></h2>
<div class="post__tags">{tag_links}</div></header><div class="post__content"><p>{excerpt}</p>
<a href="{BASE}/{slug}/" class="read-more">Read more</a></div></article>"""


@pytest.fixture
def publii(tmp_path: Path) -> Path:
    src = tmp_path / "publii"
    ld_author = {"@type": "Person", "name": "Ada Wick", "url": f"{BASE}/authors/ada/"}
    _write(
        src,
        "index.html",
        _page(
            "home-template",
            '<div class="hero framed"><p>Welcome, maker.</p></div>'
            + _article(
                "first-light",
                "First Light",
                [("glass", "Glass"), ("night-walks", "Night Walks")],
                "We lit the first lantern on a cold evening and watched the glow travel down&hellip;",
            )
            + _article("wick-notes", "Wick Notes", [("glass", "Glass")], "Short notes on wicks.")
            + '<nav class="pagination"><a href="/page/2/">Previous</a></nav>',
            head=HEAD,
            tail=f'<script defer src="https://umami.example/script.js" data-website-id="{WID}"></script><script>console.log("footer code")</script>',
        ),
    )
    _write(
        src,
        "page/2/index.html",
        _page(
            "home-template", _article("old-post", "Old Post", [], "An older post, hand summary.")
        ),
    )
    body = f"""<p>We lit the <strong>first lantern </strong>on a cold evening, see <a href="{BASE}/about/">about</a>
and <a href="{BASE}/wick-notes/#trimming">trimming</a>.</p><h2 id="glass">Glass</h2>
<figure class="post__image post__image--center"><img loading="lazy" src="{BASE}/media/posts/3/lamp.png" alt="A lamp"
srcset="{BASE}/media/posts/3/responsive/lamp-sm.png 480w"><figcaption>Our first lamp</figcaption></figure>
<ul><li>Clear glass</li><li>Smoked glass</li></ul><blockquote><p>Light one lamp.</p><p>Then another.</p></blockquote>
<p>Download the <a href="{BASE}/media/files/plan.pdf">plan</a>.</p><iframe src="https://video.example/embed/1"></iframe>"""
    _write(
        src,
        "first-light/index.html",
        _page(
            "post-template",
            f"""<article class="post"><header><h1 class="post__title">First Light</h1>
<div class="post__tags"><a href="{BASE}/tags/glass/" class="invert">Glass</a> <a href="{BASE}/tags/night-walks/" class="invert">Night Walks</a></div>
</header><div class="post__entry">{body}</div><footer class="post__footer"><div class="post__share">Share</div></footer></article>""",
            ld={
                "@type": "Article",
                "headline": "First Light",
                "datePublished": "2026-03-01T19:30-06:00",
                "dateModified": "2026-04-02T08:00-05:00",
                "description": "How the club began.",
                "author": ld_author,
            },
        ),
    )
    _write(
        src,
        "wick-notes/index.html",
        _page(
            "post-template",
            '<article class="post"><header><h1>Wick Notes</h1></header><div class="post__entry"><h2 id="trimming">Trimming</h2><p>Trim often.</p></div></article>',
            ld={
                "@type": "Article",
                "headline": "Wick Notes",
                "datePublished": "2026-02-01T10:00-06:00",
                "dateModified": "2026-02-01T10:05-06:00",
                "author": ld_author,
            },
        ),
    )
    _write(
        src,
        "old-post/index.html",
        _page(
            "post-template",
            '<article class="post"><header><h1>Old Post</h1></header><div class="post__entry"><p>An older post, with more words than its summary.</p></div></article>',
            ld={
                "@type": "Article",
                "headline": "Old Post",
                "datePublished": "2025-01-01T10:00-06:00",
                "author": {"@type": "Person", "name": "Bo Flint", "url": f"{BASE}/authors/bo/"},
            },
        ),
    )
    _write(
        src,
        "about/index.html",
        _page(
            "page-template",
            '<article class="post"><header><h1>About</h1></header><div class="post__entry"><p>We make lanterns.</p></div></article>',
            ld={
                "@type": "Article",
                "headline": "About",
                "datePublished": "2025-06-01T10:00-05:00",
                "author": ld_author,
            },
        ),
    )
    _write(
        src,
        "tags/glass/index.html",
        _page(
            "tag-template",
            '<div class="page__header"><h1>#Glass</h1><div class="page__desc">All about glass.</div></div>',
        ),
    )
    _write(src, "tags/index.html", _page("tags-template", "<ul></ul>"))
    _write(
        src,
        "authors/ada/index.html",
        _page(
            "author-template",
            f'<div class="page--author__header"><img src="{BASE}/media/website/ada.png" class="page--author__avatar" alt="Ada"><h1>Ada Wick <sup>(2 Posts)</sup></h1></div>',
        ),
    )
    _write(src, "authors/ada/page/2/index.html", _page("author-template", "<p>more</p>"))
    _write(src, "404.html", "<html></html>")
    _write(src, "media/posts/3/lamp.png", PNG)
    _write(src, "media/posts/3/responsive/lamp-sm.png", PNG)
    _write(src, "media/files/plan.pdf", b"%PDF-1.4\n")
    _write(src, "media/website/icon.png", PNG)
    _write(src, "media/website/ada.png", PNG)
    _write(
        src,
        "assets/css/style.css",
        ":root{--page-width:95%;--accent:200, 80%, 50%;--background:248, 11%, 15%;--color:0,0%,100%}body{}",
    )
    feed = {
        "items": [
            {
                "url": f"{BASE}/first-light/",
                "summary": "We lit the first lantern on a cold evening and watched the glow travel down&hellip;",
                "tags": ["Night Walks", "Glass"],
            },
            {"url": f"{BASE}/wick-notes/", "summary": "Short notes on wicks.", "tags": []},
        ]
    }
    _write(src, "feed.json", json.dumps(feed))
    return src


def test_import_reads_everything_publii_published(publii: Path, tmp_path: Path) -> None:
    dest = tmp_path / "site"
    report = import_publii(publii, dest, timezone="America/Chicago")
    assert (report.posts, report.pages, report.authors) == (3, 1, 2)
    cfg = tomlkit.parse((dest / "site.toml").read_text(encoding="utf-8"))
    assert cfg["site"]["title"] == "Lantern Club" and cfg["site"]["language"] == "en"
    assert cfg["site"]["timezone"] == "America/Chicago" and cfg["site"]["author"] == "ada"
    assert (
        cfg["platforms"]["web"]["base_url"] == BASE and cfg["platforms"]["web"]["pagination"] == 2
    )
    assert cfg["urls"]["post"] == "/{slug}/"
    menu = cfg["menu"]["items"]
    assert menu[0]["ref"] == "home"
    assert menu[1]["label"] == "About us" and menu[1]["children"][0]["ref"] == "page:about"
    assert (
        menu[1]["children"][1]["url"] == "https://elsewhere.example/"
        and menu[2]["ref"] == "tag:glass"
    )
    assert cfg["footer"]["text"] == "Lantern Club"
    assert cfg["render"]["summary_words"] == 15
    assert cfg["analytics"]["umami"]["website_id"] == WID
    head = cfg["custom_html"]["head"]
    assert "stats.example/gtag.js" in head and 'id="chat-button"' in head
    assert WID not in head and "img[loading]" not in head
    assert 'console.log("footer code")' in cfg["custom_html"]["body_end"]
    assert "scripts.min.js" not in cfg["custom_html"]["body_end"]
    assert (
        cfg["redirects"][0]["from"] == "/authors/ada/page/2/"
        and cfg["redirects"][0]["to"] == "/authors/ada/"
    )
    assert cfg["theme"]["settings"]["welcome"] == "Welcome, maker."
    assert cfg["theme"]["settings"]["accent"] == "#19a1e6"
    assert "background" not in cfg["theme"]["settings"]  # same as the theme's default
    ada = next(a for a in cfg["authors"] if a["id"] == "ada")
    assert ada["name"] == "Ada Wick" and ada["avatar"] == "/authors/ada.png"
    assert (dest / "media" / "authors" / "ada.png").is_file()
    assert (dest / "media" / "favicon.png").is_file()
    assert (dest / "media" / "files" / "plan.pdf").is_file()
    assert "All about glass." in (dest / "content" / "tags" / "glass.md").read_text(
        encoding="utf-8"
    )


def test_import_converts_posts_faithfully(publii: Path, tmp_path: Path) -> None:
    dest = tmp_path / "site"
    import_publii(publii, dest)
    post = (dest / "content" / "posts" / "2026-03-01-first-light" / "index.md").read_text(
        encoding="utf-8"
    )
    assert 'date: "2026-03-01T19:30:00-06:00"' in post
    assert 'updated: "2026-04-02T08:00:00-05:00"' in post
    assert 'tags: ["Glass", "Night Walks"]' in post  # in the order the page showed them
    assert '"description": "How the club began."' in post
    assert "summary:" not in post  # an automatic excerpt: left to summary_words
    assert "the **first lantern** on" in post
    assert "[about](/about/)" in post and "[trimming](/wick-notes/#trimming)" in post
    assert "![A lamp](lamp.png)" in post and "*Our first lamp*" in post
    assert "- Clear glass" in post and "> Light one lamp.\n>\n> Then another." in post
    assert "[plan](/media/files/plan.pdf)" in post
    assert '<iframe src="https://video.example/embed/1"></iframe>' in post
    assert "Share" not in post  # Publii's share box is furniture, not writing
    assert (dest / "content" / "posts" / "2026-03-01-first-light" / "lamp.png").is_file()
    assert not (dest / "content" / "posts" / "2026-03-01-first-light" / "lamp-sm.png").exists()
    wick = (dest / "content" / "posts" / "2026-02-01-wick-notes" / "index.md").read_text(
        encoding="utf-8"
    )
    assert 'summary: "Short notes on wicks."' in wick and "updated:" not in wick
    old = (dest / "content" / "posts" / "2025-01-01-old-post" / "index.md").read_text(
        encoding="utf-8"
    )
    assert 'summary: "An older post, hand summary."' in old and 'author: "bo"' in old
    assert (dest / "content" / "pages" / "about.md").is_file()


def test_imported_site_builds_with_the_old_addresses(publii: Path, tmp_path: Path) -> None:
    dest = tmp_path / "site"
    import_publii(publii, dest, timezone="America/Chicago")
    build = build_site(load_site(dest), now=NOW)
    web = build.platforms["web"]
    errors = [w for w in web.warnings if w.severity == "error"]
    assert errors == []
    for path in (
        "first-light/index.html",
        "about/index.html",
        "tags/glass/index.html",
        "authors/ada/index.html",
        "favicon.png",
    ):
        assert path in web.files, path
    page = web.files["first-light/index.html"].data.decode()
    assert f'data-website-id="{WID}"' in page and 'data-domains="example.org"' in page
    assert "This article was updated on" in page


def test_cli_imports_and_refuses_bad_input(
    publii: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "cli-site"
    assert main(["import", "publii", str(publii), str(dest)]) == 0
    out = capsys.readouterr().out
    assert "3 posts, 1 pages" in out and "constellation check" in out
    assert main(["import", "publii", str(publii), str(dest)]) == 1
    assert "already has files" in capsys.readouterr().err
    not_publii = tmp_path / "plain"
    _write(not_publii, "index.html", "<html><head><title>x</title></head></html>")
    assert not looks_like_publii(not_publii)
    with pytest.raises(PubliiImportError, match="doesn't look like a site built by Publii"):
        import_publii(not_publii, tmp_path / "x")


def test_html_to_markdown_handles_what_editors_write() -> None:
    md = html_to_markdown(
        "<p>A <em>b </em>c_d *e* [f]</p><ol start='3'><li>three</li><li>four<ul><li>nested</li></ul></li></ol>"
        "<pre><code class='language-py'>x = 1\n</code></pre><table><tr><th>A</th><th>B|C</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<p>line<br>break</p><hr><h3>Head</h3>"
    )
    assert "A *b* c\\_d \\*e\\* \\[f\\]" in md
    assert "3. three\n4. four\n   - nested" in md
    assert "```py\nx = 1\n```" in md
    assert "| A | B\\|C |\n|---|---|\n| 1 | 2 |" in md
    assert "line<br>\nbreak" in md and "---" in md and "### Head" in md
