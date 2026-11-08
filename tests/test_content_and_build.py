from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.core.build.pipeline import build_site, diff_manifests, write_build
from constellation.core.content import ContentError, load_site

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 9, tzinfo=UTC)


def test_demo_site_loads() -> None:
    site = load_site(DEMO)
    assert [p.slug for p in site.posts][:2] == ["scheduled-for-later", "rebuilding-the-workbench"]
    assert site.post_by_slug("a-draft").meta.status == "draft"
    assert site.tags["foundations"].meta.description.startswith("The posts")
    assert [p.slug for p in site.series["the-rites"].posts] == ["the-veiled-ascent", "the-rites-of-ascension"]
    assert site.page_by_slug("about") is not None


def test_drafts_and_future_posts_are_excluded_but_hidden_pages_are_built() -> None:
    build = build_site(load_site(DEMO), now=NOW)
    web = build.platforms["web"].files
    gem = build.platforms["gemini"].files
    assert "posts/a-draft/index.html" not in web
    assert "posts/scheduled-for-later/index.html" not in web
    assert "posts/scheduled-for-later.gmi" not in gem
    assert "hidden-page/index.html" in web
    assert "hidden-page.gmi" in gem
    assert "Scheduled" not in gem["index.gmi"].data.decode()
    assert "Hidden Page" not in gem["index.gmi"].data.decode()


def test_future_post_appears_after_its_date() -> None:
    build = build_site(load_site(DEMO), now=datetime(2027, 1, 1, tzinfo=UTC))
    assert "posts/scheduled-for-later.gmi" in build.platforms["gemini"].files


def test_build_is_deterministic_and_release_id_ignores_time() -> None:
    a = build_site(load_site(DEMO), now=NOW)
    b = build_site(load_site(DEMO), now=datetime(2026, 11, 10, tzinfo=UTC))
    assert a.manifest()["platforms"] == b.manifest()["platforms"]
    assert a.release_id() == b.release_id()
    assert a.manifest()["built_at"] != b.manifest()["built_at"]


def test_gemsub_index_rules() -> None:
    build = build_site(load_site(DEMO), now=NOW)
    text = build.platforms["gemini"].files["index.gmi"].data.decode()
    lines = text.split("\n")
    assert lines[0].startswith("# ")
    entries = [ln for ln in lines if ln.startswith("=> /posts/")]
    assert all(ln.split(" ", 2)[2][:10].count("-") == 2 for ln in entries), entries
    assert entries[0].endswith("2026-11-08 Rebuilding the Workbench")


def test_web_pagination_and_media() -> None:
    build = build_site(load_site(DEMO), now=NOW)
    web = build.platforms["web"].files
    assert "page/2/index.html" in web
    assert "media/posts/rebuilding-the-workbench/workbench.png" in web
    assert "media/logo.png" in web
    assert web["media/logo.png"].media_type == "image/png"
    assert "assets/base/style.css" in web


def test_html_output_has_metadata_and_web_only_block() -> None:
    build = build_site(load_site(DEMO), now=NOW)
    page = build.platforms["web"].files["posts/rebuilding-the-workbench/index.html"].data.decode()
    assert '<link rel="canonical" href="https://demo.example.org/posts/rebuilding-the-workbench/">' in page
    assert 'class="web-only"' in page
    assert "Gemini readers: start with" not in page
    assert "<!-- tracking snippets go here -->" in page
    assert 'Also on Gemini: <a href="gemini://demo.example.org/posts/rebuilding-the-workbench.gmi">' in page


def test_write_build_and_manifest(tmp_path: Path) -> None:
    build = build_site(load_site(DEMO), now=NOW)
    write_build(build, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert (tmp_path / "gemini" / "posts" / "the-call.gmi").is_file()
    assert (tmp_path / "web" / "posts" / "the-call" / "index.html").is_file()
    entry = manifest["platforms"]["gemini"]["posts/the-call.gmi"]
    assert entry["type"] == "text/gemini" and len(entry["sha256"]) == 64
    d = diff_manifests(manifest["platforms"]["web"], {**manifest["platforms"]["web"], "new.txt": {"sha256": "x"}})
    assert d["added"] == ["new.txt"] and not d["removed"]


def test_broken_internal_links_are_errors(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(DEMO, tmp_path / "site")
    post = tmp_path / "site" / "content" / "posts" / "2026-11-01-broken"
    post.mkdir()
    (post / "index.md").write_text("---\ntitle: Broken\n---\n\nSee [x](post:nope) and [d](post:a-draft) and ![i](missing.png).\n")
    build = build_site(load_site(tmp_path / "site"), now=NOW)
    codes = sorted({w.code for w in build.platforms["gemini"].warnings if w.severity == "error"})
    assert codes == ["LINK-BROKEN-INTERNAL", "LINK-BROKEN-MEDIA", "LINK-UNPUBLISHED"]


def test_content_errors_are_collected(tmp_path: Path) -> None:
    site = tmp_path / "s"
    (site / "content" / "posts").mkdir(parents=True)
    (site / "site.toml").write_text('[site]\ntitle = "S"\n')
    (site / "content" / "posts" / "no-date.md").write_text("---\ntitle: X\n---\n")
    (site / "content" / "posts" / "bad-slug.md").write_text("---\ntitle: Y\nslug: Not Valid\ndate: 2026-01-01\n---\n")
    with pytest.raises(ContentError) as e:
        load_site(site)
    msgs = "\n".join(e.value.problems)
    assert "no date" in msgs and "slug" in msgs


def test_cli_build_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from constellation.cli import main

    assert main(["build", str(DEMO), "--out", str(tmp_path / "out"), "--now", "2026-11-09T00:00:00+00:00"]) == 0
    assert (tmp_path / "out" / "manifest.json").is_file()
    assert main(["check", str(DEMO)]) == 0
    out = capsys.readouterr().out
    assert "HEADING-TOO-DEEP" in out
