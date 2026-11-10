"""Reader features: galleries and the image viewer, tables of contents, related and featured posts,
share links, comments, and site search."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from constellation.core.build import features
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import Comments, Features, RenderGemini, RenderGopher
from constellation.core.render.gemtext import GemtextRenderer
from constellation.core.render.gopher import GopherRenderer, GopherTarget
from constellation.core.render.html import HtmlRenderer
from constellation.importers.html_to_md import html_to_markdown

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
WORKBENCH = "content/posts/2026-11-08-rebuilding-the-workbench/index.md"
CALL = "content/posts/2026-10-21-the-call/index.md"


def _site(tmp_path: Path, **tables: object) -> Path:
    root = tmp_path / "site"
    shutil.copytree(DEMO, root)
    doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
    for key, value in tables.items():
        doc[key] = value
    (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    return root


def _front(root: Path, rel: str, extra: str = "", body_prefix: str = "") -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    head, _, rest = text.partition("\n---\n")
    p.write_text(
        head + ("\n" + extra if extra else "") + "\n---\n" + body_prefix + rest, encoding="utf-8"
    )


def _web(root: Path, path: str) -> str:
    build = build_site(load_site(root), now=NOW)
    return build.platforms["web"].files[path].data.decode("utf-8")


GALLERY = """Before.

::: gallery 2
![One](a.png "First")
![Two](https://example.org/b.jpg)
:::
"""


def _u(ref: object) -> str:
    raw = getattr(ref, "raw", "")
    return raw if "://" in raw else "/media/" + raw


# ---- galleries ---------------------------------------------------------------------------------
def test_gallery_on_the_web_links_each_image_for_the_viewer() -> None:
    res = HtmlRenderer(_u).render(parse_markdown(GALLERY))
    html = res.text
    assert '<div class="gallery" style="--gallery-cols:2">' in html
    assert html.count('data-lightbox="gallery-1"') == 2
    assert '<a href="/media/a.png" data-lightbox="gallery-1" data-caption="First">' in html
    assert "<figcaption>First</figcaption>" in html
    assert not res.warnings


def test_gallery_on_gemini_and_gopher_is_a_list_of_image_links() -> None:
    doc = parse_markdown(GALLERY)
    gem = GemtextRenderer(RenderGemini(), _u).render(doc).text
    assert "=> /media/a.png" in gem and "=> https://example.org/b.jpg" in gem
    gopher = GopherRenderer(RenderGopher(), _u, GopherTarget("example.org", 70)).render(doc).text
    assert "[Image: One]" in gopher and "[Image: Two]" in gopher


def test_gallery_warns_about_things_that_are_not_images() -> None:
    res = HtmlRenderer(_u).render(parse_markdown("::: gallery\n![A](a.png)\n\nsome words\n:::\n"))
    assert [w.code for w in res.warnings] == ["GALLERY-NOT-IMAGE"]
    alt = HtmlRenderer(_u).render(parse_markdown("::: gallery\n![](a.png)\n:::\n"))
    assert [w.code for w in alt.warnings] == ["IMG-NO-ALT"]


def test_publii_gallery_becomes_a_gallery_block() -> None:
    md = html_to_markdown(
        '<div class="gallery-wrapper"><div class="gallery" data-columns="3">'
        '<figure class="gallery__item"><a href="media/g/a.jpg"><img src="media/g/a-thumbnail.jpg" alt="A"></a>'
        "<figcaption>Cap <b>tion</b></figcaption></figure>"
        '<figure class="gallery__item"><a href="media/g/b.jpg"><img src="t.jpg" alt=""></a></figure>'
        "</div></div><p>After</p>"
    )
    assert md.startswith('::: gallery 3\n![A](media/g/a.jpg "Cap tion")\n![](media/g/b.jpg)\n:::')
    assert md.rstrip().endswith("After")


def test_viewer_script_and_styles_are_on_every_page_in_any_theme(tmp_path: Path) -> None:
    root = _site(tmp_path)
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"]
    assert "assets/constellation/features.js" in web.files
    assert "assets/constellation/features.css" in web.files
    pages = [
        f for f in web.files.values() if f.source is None and f.media_type.startswith("text/html")
    ]
    assert pages
    for f in pages:
        html = f.data.decode()
        assert html.count("/assets/constellation/features.js") == 1, f.path
        # Linked before the theme's stylesheet, so the theme wins.
        assert html.index("features.css") < html.index("style.css"), f.path
    off = _site(tmp_path / "off", features={"lightbox": False})
    assert 'features.js" data-lightbox="off"' in _web(off, "index.html")


# ---- table of contents -------------------------------------------------------------------------
def test_table_of_contents_follows_the_site_setting_and_the_post(tmp_path: Path) -> None:
    body = "## One\n\ntext\n\n### One point five\n\n## Two\n\ntext\n\n## Two\n\n"
    root = _site(tmp_path, features={"toc": True, "toc_min_headings": 3})
    _front(root, CALL, body_prefix=body)
    html = _web(root, "posts/the-call/index.html")
    toc = re.search(r'<nav class="toc".*?</nav>', html, re.S)
    assert toc is not None
    assert '<li class="toc-1"><a href="#one">One</a></li>' in toc.group(0)
    assert '<li class="toc-2"><a href="#one-point-five">One point five</a></li>' in toc.group(0)
    assert 'href="#two-2"' in toc.group(0) and 'id="two-2"' in html  # the same ids as the headings
    # Too few headings: no table; toc: true in the post forces one.
    workbench = _web(root, "posts/rebuilding-the-workbench/index.html")
    assert 'class="toc"' not in workbench
    _front(root, WORKBENCH, "toc: true", body_prefix="## The only heading\n\n")
    assert 'class="toc"' in _web(root, "posts/rebuilding-the-workbench/index.html")
    _front(root, CALL, "toc: false")
    assert 'class="toc"' not in _web(root, "posts/the-call/index.html")


def test_no_table_of_contents_by_default(tmp_path: Path) -> None:
    root = _site(tmp_path)
    _front(root, CALL, body_prefix="## A\n\n## B\n\n## C\n\n## D\n\n")
    assert 'class="toc"' not in _web(root, "posts/the-call/index.html")


# ---- related and featured ----------------------------------------------------------------------
def test_related_posts_share_tags_newest_first(tmp_path: Path) -> None:
    site = load_site(_site(tmp_path))
    listed = site.published_posts(NOW)
    call = next(p for p in listed if p.slug == "the-call")
    assert [p.slug for p in features.related_posts(call, listed, 3)] == ["the-veiled-ascent"]
    assert features.related_posts(call, listed, 0) == []
    html = _web(_site(tmp_path / "b"), "posts/the-call/index.html")
    assert 'class="related-posts"' in html and "/posts/the-veiled-ascent/" in html
    gem = build_site(load_site(_site(tmp_path / "c")), now=NOW).platforms["gemini"]
    text = next(f.data.decode() for p, f in gem.files.items() if p.startswith("posts/the-call"))
    assert "### Related posts" in text


def test_featured_posts_are_pinned_to_the_top(tmp_path: Path) -> None:
    root = _site(tmp_path)
    _front(root, CALL, "featured: true")
    build = build_site(load_site(root), now=NOW)
    home = build.platforms["web"].files["index.html"].data.decode()
    first = re.search(r'<article class="post-summary[^"]*">.*?href="([^"]+)"', home, re.S)
    assert first is not None and first.group(1) == "/posts/the-call/"
    assert '<span class="featured-label">Featured</span>' in home
    gem = build.platforms["gemini"].files["index.gmi"].data.decode()
    links = [ln for ln in gem.splitlines() if ln.startswith("=> /posts/")]
    assert links[0].startswith("=> /posts/the-call")
    unpinned = _site(tmp_path / "u", features={"pin_featured": False})
    _front(unpinned, CALL, "featured: true")
    home2 = _web(unpinned, "index.html")
    first2 = re.search(r'<article class="post-summary[^"]*">.*?href="([^"]+)"', home2, re.S)
    assert first2 is not None and first2.group(1) != "/posts/the-call/"


# ---- share links -------------------------------------------------------------------------------
def test_share_links_are_plain_escaped_links() -> None:
    links = features.share_links(
        ["mastodon", "x", "email", "copy"], "https://e.org/a b?c=1&d", 'Hi & "you"'
    )
    by = {link["network"]: link["url"] for link in links}
    assert (
        by["x"]
        == "https://x.com/intent/post?text=Hi%20%26%20%22you%22&url=https%3A%2F%2Fe.org%2Fa%20b%3Fc%3D1%26d"
    )
    assert by["mastodon"].startswith("https://s2f.kytta.dev/?text=Hi%20%26")
    assert by["email"].startswith("mailto:?subject=")
    assert by["copy"] == "https://e.org/a b?c=1&d"
    assert features.share_links(["x"], "", "t") == []


def test_share_links_on_posts_only(tmp_path: Path) -> None:
    root = _site(tmp_path, features={"share": ["bluesky", "copy"]})
    post = _web(root, "posts/the-call/index.html")
    assert 'class="share-links"' in post
    assert 'href="https://bsky.app/intent/compose?text=' in post
    assert 'data-copy-url="https://demo.example.org/posts/the-call/"' in post
    assert "share-links" not in _web(root, "about/index.html")
    assert "share-links" not in _web(_site(tmp_path / "none"), "posts/the-call/index.html")


def test_unknown_share_network_is_refused() -> None:
    with pytest.raises(ValidationError, match="unknown share network"):
        Features(share=["myspace"])
    assert Features(share=["x", "x", "email"]).share == ["x", "email"]


# ---- comments ----------------------------------------------------------------------------------
GISCUS = {
    "provider": "giscus",
    "repo": "me/site",
    "repo_id": "R_1",
    "category": "Comments",
    "category_id": "DIC_2",
}


def test_giscus_comments(tmp_path: Path) -> None:
    html = _web(_site(tmp_path, comments=GISCUS), "posts/the-call/index.html")
    assert 'src="https://giscus.app/client.js" data-repo="me/site" data-repo-id="R_1"' in html
    assert 'data-category-id="DIC_2"' in html and 'data-mapping="pathname"' in html
    root = _site(tmp_path / "off", comments=GISCUS)
    _front(root, CALL, "comments: false")
    assert "giscus" not in _web(root, "posts/the-call/index.html")
    assert "giscus" not in _web(root, "about/index.html")  # never on pages


def test_disqus_comments_quote_their_values(tmp_path: Path) -> None:
    html = _web(
        _site(tmp_path, comments={"provider": "disqus", "shortname": "my-site"}),
        "posts/the-call/index.html",
    )
    assert '"https://" + "my-site" + ".disqus.com/embed.js"' in html
    assert 'this.page.url = "https://demo.example.org/posts/the-call/"' in html
    with pytest.raises(ValidationError):
        Comments(provider="disqus", shortname='x"; alert(1); "')


def test_mastodon_comments_need_a_toot(tmp_path: Path) -> None:
    root = _site(tmp_path, comments={"provider": "mastodon"})
    assert "cms-mastodon-comments" not in _web(root, "posts/the-call/index.html")
    _front(root, CALL, 'mastodon: "https://mastodon.social/@someone/112233"')
    build = build_site(load_site(root), now=NOW)
    html = build.platforms["web"].files["posts/the-call/index.html"].data.decode()
    assert 'class="cms-mastodon-comments" data-host="mastodon.social" data-id="112233"' in html
    gem = next(
        f.data.decode()
        for p, f in build.platforms["gemini"].files.items()
        if p.startswith("posts/the-call")
    )
    assert "=> https://mastodon.social/@someone/112233 Comments on Mastodon" in gem
    _front(root, WORKBENCH, 'mastodon: "https://mastodon.social/about"')
    codes = [w.code for w in build_site(load_site(root), now=NOW).warnings]
    assert "COMMENTS-MASTODON-URL" in codes


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://mastodon.social/@me/1234567", True),
        ("https://hachyderm.io/users/me/statuses/99", True),
        ("https://x.example/@me@other.example/5", True),
        ("http://mastodon.social/@me/1", False),
        ("https://mastodon.social/@me", False),
        ("https://evil.example/@me/1/../../x", False),
        ("javascript:alert(1)//mastodon.social/@a/1", False),
    ],
)
def test_toot_addresses(url: str, ok: bool) -> None:
    assert (features.mastodon_toot(url) is not None) is ok


def test_custom_comments_and_incomplete_settings(tmp_path: Path) -> None:
    html = _web(
        _site(tmp_path, comments={"provider": "custom", "html": '<div id="talk"></div>'}),
        "posts/the-call/index.html",
    )
    assert '<div id="talk"></div>' in html
    partial = _web(
        _site(tmp_path / "p", comments={"provider": "giscus", "repo": "me/site"}),
        "posts/the-call/index.html",
    )
    assert 'id="comments"' not in partial  # missing ids: nothing rather than a broken widget
    with pytest.raises(ValidationError, match="owner/repository"):
        Comments(provider="giscus", repo="not a repo")


# ---- search ------------------------------------------------------------------------------------
def test_search_page_and_index(tmp_path: Path) -> None:
    root = _site(tmp_path)
    build = build_site(load_site(root), now=NOW)
    web = build.platforms["web"]
    assert "search/index.html" in web.files
    index = json.loads(web.files["search.json"].data)
    urls = {item["u"] for item in index["items"]}
    assert "/posts/the-call/" in urls and "/about/" in urls
    assert not any(
        "draft" in u or "scheduled" in u for u in urls
    )  # drafts and future posts stay out
    assert not any("hidden-page" in u for u in urls)  # hidden pages are not listed
    page = web.files["search/index.html"].data.decode()
    assert 'id="cms-search"' in page and '<meta name="robots" content="noindex">' in page
    home = web.files["index.html"].data.decode()
    assert 'class="site-search"' in home and 'action="/search/"' in home
    assert "search.json" not in build.platforms["gemini"].files


def test_search_can_be_turned_off_and_yields_to_a_page(tmp_path: Path) -> None:
    off = build_site(load_site(_site(tmp_path, features={"search": False})), now=NOW)
    assert "search.json" not in off.platforms["web"].files
    assert "site-search" not in off.platforms["web"].files["index.html"].data.decode()
    root = _site(tmp_path / "taken")
    (root / "content" / "pages" / "search.md").write_text(
        "---\ntitle: Search\n---\nMine.\n", encoding="utf-8"
    )
    build = build_site(load_site(root), now=NOW)
    assert "SEARCH-PATH-TAKEN" in [w.code for w in build.warnings]
    assert "Mine." in build.platforms["web"].files["search/index.html"].data.decode()
    assert not [w for w in build.warnings if w.code == "PATH-COLLISION"]
