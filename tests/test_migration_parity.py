"""What a site moved over from Publii (fractum.faith) needs: posts and pages sharing /{slug}/,
author pages, word-count summaries, a favicon, and the fractum theme's matching details."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import tomlkit

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import SiteConfig
from constellation.core.render.plaintext import summary_text
from constellation.core.routes import RefResolver

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
THEMES = Path(__file__).resolve().parent.parent / "src" / "constellation" / "themes_builtin"


def _site(tmp_path: Path, theme: str = "fractum", **changes: object) -> Path:
    root = tmp_path / "site"
    shutil.copytree(DEMO, root)
    toml = root / "site.toml"
    doc = tomlkit.parse(toml.read_text(encoding="utf-8"))
    doc["site"]["theme"] = theme
    for dotted, value in changes.items():
        node = doc
        *parents, last = dotted.split("__")
        for part in parents:
            if part not in node:
                node[part] = tomlkit.table()
            node = node[part]
        node[last] = value
    toml.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return root


# ---- posts and pages at the same address ---------------------------------------------------------
def test_links_find_pages_when_posts_and_pages_share_slash_slug() -> None:
    cfg = SiteConfig.model_validate(
        {
            "site": {"title": "T"},
            "urls": {"post": "/{slug}/"},
            "platforms": {"web": {"hostnames": ["x.org"]}},
        }
    )
    r = RefResolver(cfg, post_slugs={"a-post"}, page_slugs={"a-page"})
    assert r.resolve("/a-page/").kind == "page"
    assert r.resolve("https://x.org/a-page/").kind == "page"
    assert r.resolve("/a-post/").kind == "post"
    assert r.resolve("a-page").kind == "page"
    assert r.resolve("/unknown/").kind == "post"  # still reported as a broken post link


def test_build_with_root_post_urls_has_no_false_broken_links(tmp_path: Path) -> None:
    root = _site(tmp_path, urls__post="/{slug}/")
    post = next((root / "content" / "posts").glob("*/index.md"))
    post.write_text(
        post.read_text(encoding="utf-8") + "\nSee [about](/about/).\n", encoding="utf-8"
    )
    build = build_site(load_site(root), now=NOW)
    broken = [w for w in build.platforms["web"].warnings if w.code == "LINK-BROKEN-INTERNAL"]
    assert broken == []
    assert "the-call/index.html" in build.platforms["web"].files


# ---- author pages -----------------------------------------------------------------------------------
def test_each_author_gets_a_page_on_every_platform(tmp_path: Path) -> None:
    root = _site(tmp_path)
    toml = root / "site.toml"
    doc = tomlkit.parse(toml.read_text(encoding="utf-8"))
    doc["authors"][0]["avatar"] = "/seraph.png"
    toml.write_text(tomlkit.dumps(doc), encoding="utf-8")
    (root / "media").mkdir(exist_ok=True)
    (root / "media" / "seraph.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    site = load_site(root)
    build = build_site(site, now=NOW)
    web = build.platforms["web"].files
    page = web["authors/seraph/index.html"].data.decode()
    listed = len(site.published_posts(NOW))
    assert f"Seraph <sup>({listed} Posts)</sup>" in page
    assert 'src="/media/seraph.png"' in page
    assert page.count('class="entry"') == listed
    assert "authors/index.html" in web
    assert "/authors/seraph/" in web["sitemap.xml"].data.decode()
    post = web["posts/the-call/index.html"].data.decode()
    assert '<a href="/authors/seraph/">Seraph</a>' in post
    assert "authors/seraph.gmi" in build.platforms["gemini"].files
    if "gopher" in build.platforms:
        assert "authors/seraph/gophermap" in build.platforms["gopher"].files


def test_author_links_resolve_and_the_url_pattern_is_configurable(tmp_path: Path) -> None:
    cfg = SiteConfig.model_validate({"site": {"title": "T"}, "urls": {"author": "/people/{slug}/"}})
    assert RefResolver(cfg).resolve("author:seraph").kind == "author"
    assert RefResolver(cfg).resolve("/people/seraph/").target == "seraph"
    root = _site(tmp_path, urls__author="/people/{slug}/")
    assert "people/seraph/index.html" in build_site(load_site(root), now=NOW).platforms["web"].files


# ---- summaries ---------------------------------------------------------------------------------------
def test_summary_by_word_count_reads_across_paragraphs() -> None:
    doc = parse_markdown("One two three.\n\n## Heading\n\nFour five six seven.\n")
    assert summary_text(doc, words=5) == "One two three. Four five…"
    assert summary_text(doc, words=50) == "One two three. Four five six seven."
    assert summary_text(doc) == "One two three."  # default: the first paragraph


def test_site_setting_controls_list_summaries_and_own_summary_wins(tmp_path: Path) -> None:
    root = _site(tmp_path, render__summary_words=3)
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"].files
    lists = [f.data.decode() for path, f in web.items() if path.endswith("index.html")]
    summaries = {
        s for page in lists for s in re.findall(r'<p class="entry__summary">(.*?)</p>', page)
    }
    own = "The world is broken. We exist to mend it."  # "The Call" has its own summary
    assert own in summaries
    others = [s for s in summaries if s != own]
    assert others and all(len(s.split()) <= 3 for s in others)
    assert any(s.endswith("…") for s in others)


# ---- favicon -----------------------------------------------------------------------------------------
def test_a_favicon_in_media_is_linked_by_both_themes(tmp_path: Path) -> None:
    for theme in ("fractum", "base"):
        root = _site(tmp_path / theme, theme)
        (root / "media").mkdir(exist_ok=True)
        (root / "media" / "favicon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        web = build_site(load_site(root), now=NOW).platforms["web"].files
        assert "favicon.png" in web
        assert '<link rel="icon" href="/favicon.png">' in web["index.html"].data.decode()
    plain = build_site(load_site(_site(tmp_path / "none")), now=NOW).platforms["web"].files
    assert 'rel="icon"' not in plain["index.html"].data.decode()


# ---- the fractum theme, as on the original site -------------------------------------------------------
def test_fractum_post_footer_matches_the_original(tmp_path: Path) -> None:
    root = _site(tmp_path)
    post = root / "content" / "posts" / "2026-10-21-the-call" / "index.md"
    text = post.read_text(encoding="utf-8")
    post.write_text(
        text.replace(
            "date: 2026-10-21T18:56:00\n",
            "date: 2026-10-21T18:56:00\nupdated: 2026-10-30T09:00:00\n",
            1,
        ),
        encoding="utf-8",
    )
    web = build_site(load_site(root), now=NOW).platforms["web"].files
    page = web["posts/the-call/index.html"].data.decode()
    assert "This article was updated on" in page and "October 30, 2026" in page
    assert "Read other posts" in page
    assert 'class="btn" rel="next"' in page or 'class="btn" rel="prev"' in page
    off = _site(tmp_path / "off", theme__settings__show_updated=False)
    (off / "content/posts/2026-10-21-the-call/index.md").write_text(
        post.read_text(encoding="utf-8"), encoding="utf-8"
    )
    page_off = (
        build_site(load_site(off), now=NOW)
        .platforms["web"]
        .files["posts/the-call/index.html"]
        .data.decode()
    )
    assert "This article was updated on" not in page_off


def test_fractum_lists_index_404_menu_and_pagination(tmp_path: Path) -> None:
    root = _site(tmp_path, platforms__web__pagination=1)
    web = build_site(load_site(root), now=NOW).platforms["web"].files
    home = web["index.html"].data.decode()
    assert 'class="btn pagination__older"' in home and "Older" in home
    assert (
        '<button class="menu-toggle" type="button" aria-controls="site-nav" aria-expanded="false">Menu</button>'
        in home
    )
    assert 'id="site-nav"' in home
    tags = web["tags/index.html"].data.decode()
    assert re.search(r"Tags <sup>\(\d+\)</sup>", tags) and re.search(
        r"<sup>\(\d+ Posts?\)</sup>", tags
    )
    nf = web["404.html"].data.decode()
    assert "Page not found" in nf and "← Go home" in nf
    css = (THEMES / "fractum" / "static" / "style.css").read_text(encoding="utf-8")
    # The terms page sets class="terms" on <body> too; the spacing rule must target the section.
    assert "section.terms" in css and ", .terms," not in css
