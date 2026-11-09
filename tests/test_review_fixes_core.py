"""Regressions for the post-0.1.0 review of loading, rendering and building."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.core.build.pipeline import build_site, css_value, write_build
from constellation.core.content import ContentError, load_site
from constellation.core.markdown.document import Ref
from tests.helpers import CFG, RESOLVER, gem, parse

NOW = datetime(2026, 6, 1, tzinfo=UTC)
SITE_TOML = """
[site]
title = "T"
timezone = "UTC"

[platforms.web]
base_url = "https://t.example"
hostnames = ["t.example"]
feeds = ["atom"]

[platforms.gemini]
base_url = "gemini://t.example"
hostnames = ["t.example"]

[platforms.gopher]
base_url = "gopher://t.example"
hostnames = ["t.example"]
feeds = ["atom"]
"""


def make_site(root: Path, files: dict[str, str | bytes], toml: str = SITE_TOML) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "site.toml").write_text(toml, encoding="utf-8")
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
    return root


def post(title: str, body: str = "Body.", **meta: str) -> str:
    extra = "".join(f"{k}: {v}\n" for k, v in meta.items())
    return f"---\ntitle: {title}\n{extra}---\n\n{body}\n"


def build(root: Path):  # type: ignore[no-untyped-def]
    return build_site(load_site(root), now=NOW)


# ---- loading ----------------------------------------------------------------------------------
def test_impossible_date_in_name_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    root = make_site(tmp_path / "s", {"content/posts/2026-02-30-bad.md": post("Bad")})
    with pytest.raises(ContentError) as e:
        load_site(root)
    assert "not a real date" in e.value.problems[0]


def test_broken_yaml_and_non_utf8_are_problems(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {
            "content/posts/2026-01-01-a.md": "---\ntitle: Foo: bar baz\n---\nx\n",
            "content/posts/2026-01-02-b.md": "---\ntitle: Caf\xe9\n---\n".encode("latin-1"),
        },
    )
    with pytest.raises(ContentError) as e:
        load_site(root)
    text = "\n".join(e.value.problems)
    assert "not valid YAML" in text and "not UTF-8" in text


def test_bare_site_builds_web_and_creates_output(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {"content/posts/2026-01-01-a.md": post("A")},
        toml='[site]\ntitle = "T"\n',
    )
    b = build(root)
    assert list(b.platforms) == ["web"]
    out = tmp_path / "deep" / "out"
    write_build(b, out)
    assert (out / "manifest.json").is_file()


# ---- links and media --------------------------------------------------------------------------
def test_links_to_taxonomy_indexes_are_not_broken(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {"content/posts/2026-01-01-a.md": post("A", "[tags](/tags/) [archive](/archive/)")},
    )
    b = build(root)
    codes = [w.code for w in b.platforms["web"].warnings]
    assert "LINK-BROKEN-INTERNAL" not in codes
    gmi = b.platforms["gemini"].files["posts/a.gmi"].data.decode()
    assert "=> /tags.gmi tags" in gmi and "=> /archive.gmi archive" in gmi


def test_index_ref_resolves_per_platform() -> None:
    ref = RESOLVER.resolve("/categories/")
    assert ref == Ref("index", "category", None, "/categories/")


def test_dot_slash_media_and_subfolder_media(tmp_path: Path) -> None:
    png = _png()
    root = make_site(
        tmp_path / "s",
        {
            "content/posts/2026-01-01-a/index.md": post("A", "![x](./p.png) ![y](img/q.png)"),
            "content/posts/2026-01-01-a/p.png": png,
            "content/posts/2026-01-01-a/img/q.png": png,
        },
    )
    b = build(root)
    web = b.platforms["web"]
    assert not [w for w in web.warnings if w.code == "LINK-BROKEN-MEDIA"]
    page = web.files["posts/a/index.html"].data.decode()
    assert 'src="/media/posts/a/p.png"' in page and 'src="/media/posts/a/img/q.png"' in page
    assert "media/posts/a/img/q.png" in web.files


def test_page_bundles_keep_their_own_media(tmp_path: Path) -> None:
    png = _png()
    root = make_site(
        tmp_path / "s",
        {
            "content/pages/one/index.md": post("One", "![a](shared.png)"),
            "content/pages/one/shared.png": png,
            "content/pages/two/index.md": post("Two", "![b](shared.png)"),
            "content/pages/two/shared.png": _png((255, 0, 0)),
        },
    )
    web = build(root).platforms["web"]
    assert (
        web.files["media/pages/one/shared.png"].source
        != web.files["media/pages/two/shared.png"].source
    )
    assert 'src="/media/pages/two/shared.png"' in web.files["two/index.html"].data.decode()


def test_external_featured_image_is_left_alone(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {"content/posts/2026-01-01-a.md": post("A", image="https://cdn.example/p.jpg")},
    )
    page = build(root).platforms["web"].files["posts/a/index.html"].data.decode()
    assert 'content="https://cdn.example/p.jpg"' in page
    assert "/media/posts/a/https" not in page


def test_missing_featured_image_is_reported(tmp_path: Path) -> None:
    root = make_site(tmp_path / "s", {"content/posts/2026-01-01-a.md": post("A", image="nope.png")})
    assert any(w.code == "LINK-BROKEN-MEDIA" for w in build(root).platforms["web"].warnings)


def test_problems_inside_drafts_do_not_block_publishing(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {"content/posts/2026-01-01-a.md": post("A", "![x](missing.png)", status="draft")},
    )
    b = build_site(load_site(root), now=NOW, include_drafts=True)
    ws = [w for w in b.platforms["web"].warnings if w.code == "LINK-BROKEN-MEDIA"]
    assert ws and all(w.severity == "warn" for w in ws) and "draft" in ws[0].message


def test_drafts_do_not_count_in_terms_or_sitemap(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {
            "content/posts/2026-01-01-a.md": post("A", tags="[shared]"),
            "content/posts/2026-01-02-b.md": post("B", tags="[shared, secret]", status="draft"),
        },
    )
    b = build(root)
    web = b.platforms["web"].files
    assert "(1)" in web["tags/index.html"].data.decode()
    sitemap = web["sitemap.xml"].data.decode()
    assert "/tags/shared/" in sitemap and "secret" not in sitemap


def test_reserved_page_slug_collision_is_an_error(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s",
        {"content/pages/tags.md": post("Tags page"), "content/posts/2026-01-01-a.md": post("A")},
    )
    assert any(w.code == "PATH-COLLISION" for w in build(root).platforms["web"].warnings)


def test_list_template_on_a_page_falls_back(tmp_path: Path) -> None:
    root = make_site(tmp_path / "s", {"content/pages/x.md": post("X", template="home")})
    web = build(root).platforms["web"]
    assert "x/index.html" in web.files
    assert any(w.code == "TEMPLATE-NOT-FOR-PAGES" for w in web.warnings)


# ---- renderers --------------------------------------------------------------------------------
def test_gophermap_quote_does_not_corrupt_the_item(tmp_path: Path) -> None:
    root = make_site(
        tmp_path / "s", {"content/posts/2026-01-01-a.md": post("A", "> Quoted words.")}
    )
    gophermap = build(root).platforms["gopher"].files["posts/a/gophermap"].data.decode()
    line = next(ln for ln in gophermap.split("\r\n") if "Quoted" in ln)
    assert line.split("\t")[0].endswith('"') and line.endswith("\t70")


def test_gopher_text_numbers_links_and_notes_distinctly(tmp_path: Path) -> None:
    from constellation.core.render.gopher import GopherRenderer, GopherTarget

    doc = parse("![pic](p.png) see [site](https://a.example)[^1]\n\n[^1]: Note.\n")
    from constellation.core.models import RenderGopher
    from constellation.core.routes import url_for

    r = GopherRenderer(
        RenderGopher(),
        lambda ref: url_for(ref, "gopher", CFG, item_slug="x"),
        GopherTarget("h", 70),
    )
    text = r.render_text(doc).text
    assert "[image: pic][1]" in text and "site[2]" in text and "[^1]" in text
    assert "[1] pic:" in text and "[2] site: https://a.example" in text


def test_gopher_feed_carries_text_not_gophermap(tmp_path: Path) -> None:
    root = make_site(tmp_path / "s", {"content/posts/2026-01-01-a.md": post("A", "Hello there.")})
    feed = build(root).platforms["gopher"].files["feed.xml"].data.decode()
    assert "Hello there." in feed and "&#9;" not in feed


def test_hard_break_inside_link_text_stays_on_the_link_line() -> None:
    out = gem("[first  \nsecond](https://x.example/)").text
    link_lines = [ln for ln in out.split("\n") if ln.startswith("=>")]
    assert link_lines == ["=> https://x.example/ first second"]
    assert out.split("\n")[-1] != "second"


# ---- themes and images ------------------------------------------------------------------------
def test_theme_settings_cannot_break_out_of_css() -> None:
    assert ";" not in css_value("red; } body { background:url(https://evil/x) } .x{")
    assert "url(" not in css_value("red; } body { background:url(https://evil/x) } .x{")
    assert css_value("#df00ff") == "#df00ff"


def test_child_theme_without_static_uses_parent_stylesheet(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    theme = ws / "themes" / "mine"
    (theme / "templates").mkdir(parents=True)
    (theme / "theme.toml").write_text(
        '[theme]\nname = "mine"\nextends = "base"\n', encoding="utf-8"
    )
    root = make_site(
        ws / "sites" / "s",
        {"content/posts/2026-01-01-a.md": post("A")},
        toml=SITE_TOML.replace('timezone = "UTC"', 'timezone = "UTC"\ntheme = "mine"'),
    )
    # The workspace's themes folder is found without being passed in.
    web = build(root).platforms["web"].files
    assert 'href="/assets/base/style.css"' in web["index.html"].data.decode()


def test_exif_rotation_is_applied_to_variants(tmp_path: Path) -> None:
    from PIL import Image

    im = Image.new("RGB", (2000, 1000), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when displayed
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    from constellation.core.build.images import make_variants

    src = tmp_path / "phone.jpg"
    src.write_bytes(buf.getvalue())
    iset = make_variants(src, "/media/phone.jpg")
    assert iset is not None and (iset.width, iset.height) == (1000, 2000)
    small = next(v for v in iset.variants if v.path.endswith("-sm.jpg"))
    assert Image.open(io.BytesIO(small.data)).size[1] > Image.open(io.BytesIO(small.data)).size[0]


def _png(color: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, "PNG")
    return buf.getvalue()
