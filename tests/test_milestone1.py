"""Feeds, taxonomy and archive pages, image variants, and the export targets."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from constellation.core.build.images import make_variants
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.export import FolderTarget, apply_export, layout_files, plan_export

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
ATOM = "{http://www.w3.org/2005/Atom}"


def _build():
    return build_site(load_site(DEMO), now=NOW)


def test_atom_feeds_are_valid_xml_with_absolute_links() -> None:
    build = _build()
    web = ET.fromstring(build.platforms["web"].files["feed.xml"].data)
    entries = web.findall(f"{ATOM}entry")
    assert len(entries) == 4
    assert (
        entries[0].find(f"{ATOM}link").get("href")
        == "https://demo.example.org/posts/rebuilding-the-workbench/"
    )
    content = entries[0].find(f"{ATOM}content")
    assert content.get("type") == "html"
    assert 'src="https://demo.example.org/media/posts/' in content.text
    assert 'src="/media' not in content.text
    gem = ET.fromstring(build.platforms["gemini"].files["feed.xml"].data)
    assert (
        gem.find(f"{ATOM}entry/{ATOM}link")
        .get("href")
        .startswith("gemini://demo.example.org/posts/")
    )
    assert gem.find(f"{ATOM}entry/{ATOM}content").get("type") == "text"


def test_json_feed_sitemap_robots_and_favicon() -> None:
    build = _build()
    web = build.platforms["web"].files
    feed = json.loads(web["feed.json"].data)
    assert feed["version"].endswith("/1.1") and len(feed["items"]) == 4
    assert feed["items"][0]["url"].startswith("https://demo.example.org/posts/")
    urls = [
        u.text
        for u in ET.fromstring(web["sitemap.xml"].data).iter(
            "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
        )
    ]
    assert (
        "https://demo.example.org/" in urls and "https://demo.example.org/tags/foundations/" in urls
    )
    assert "https://demo.example.org/hidden-page/" not in urls
    assert b"Sitemap: https://demo.example.org/sitemap.xml" in web["robots.txt"].data
    gem = build.platforms["gemini"].files
    assert gem["favicon.txt"].data.decode().strip() == "✦"
    assert b"User-agent: webproxy" in gem["robots.txt"].data


def test_taxonomy_and_archive_pages_on_both_platforms() -> None:
    build = _build()
    web = build.platforms["web"].files
    gem = build.platforms["gemini"].files
    for path in (
        "tags/index.html",
        "tags/foundations/index.html",
        "categories/projects/index.html",
        "series/the-rites/index.html",
        "archive/index.html",
        "archive/2026/index.html",
    ):
        assert path in web, path
    for path in (
        "tags.gmi",
        "tags/foundations.gmi",
        "series/the-rites.gmi",
        "archive.gmi",
        "archive/2026.gmi",
    ):
        assert path in gem, path
    tags = gem["tags.gmi"].data.decode()
    assert "=> /tags/foundations.gmi #Foundations (2)" in tags  # title wins; drafts not counted
    assert "=> /tags/projects.gmi #projects (1)" in tags  # otherwise the author casing is kept
    series = gem["series/the-rites.gmi"].data.decode()
    assert series.index("The Veiled Ascent") < series.index("The Rites of Ascension")
    assert "The rites, in order." in series
    assert "Scheduled for Later" not in gem["tags/foundations.gmi"].data.decode()


def test_image_variants(tmp_path: Path) -> None:
    from PIL import Image

    src = tmp_path / "wide.png"
    Image.new("RGB", (2000, 500), (200, 30, 90)).save(src)
    iset = make_variants(src, "/media/posts/x/wide.png")
    assert iset is not None
    names = sorted(v.path for v in iset.variants)
    assert names == sorted(
        [
            "/media/posts/x/wide-sm.png",
            "/media/posts/x/wide-sm.webp",
            "/media/posts/x/wide-md.png",
            "/media/posts/x/wide-md.webp",
            "/media/posts/x/wide-lg.png",
            "/media/posts/x/wide-lg.webp",
            "/media/posts/x/wide.webp",
        ]
    )
    assert iset.srcset().endswith("/media/posts/x/wide.png 2000w")
    assert "wide-sm.png 480w" in iset.srcset()
    small = tmp_path / "small.png"
    Image.new("RGB", (100, 100)).save(small)
    sset = make_variants(small, "/media/small.png")
    assert (
        sset is not None
        and sset.srcset() == ""
        and [v.path for v in sset.variants] == ["/media/small.webp"]
    )


def test_variants_end_up_in_the_build_with_srcset(tmp_path: Path) -> None:
    import shutil

    from PIL import Image

    shutil.copytree(DEMO, tmp_path / "site")
    bundle = tmp_path / "site" / "content" / "posts" / "2026-11-08-rebuilding-the-workbench"
    Image.new("RGB", (1200, 300), (1, 2, 3)).save(bundle / "workbench.png")
    build = build_site(load_site(tmp_path / "site"), now=NOW)
    web = build.platforms["web"].files
    assert "media/posts/rebuilding-the-workbench/workbench-sm.png" in web
    assert "media/posts/rebuilding-the-workbench/workbench-md.webp" in web
    assert "media/posts/rebuilding-the-workbench/workbench-lg.png" not in web  # 1600 > 1200
    page = web["posts/rebuilding-the-workbench/index.html"].data.decode()
    assert 'srcset="/media/posts/rebuilding-the-workbench/workbench-sm.png 480w' in page
    assert (
        "workbench-sm"
        not in build.platforms["gemini"].files["posts/rebuilding-the-workbench.gmi"].data.decode()
    )


def test_folder_export_diff_and_deletion(tmp_path: Path) -> None:
    site = load_site(DEMO)
    build = _build()
    files = layout_files(build, "gemini", "plain", site)
    target = FolderTarget(tmp_path / "out")
    plan = plan_export(files, target)
    assert plan.removed == [] and len(plan.added) == len(files.files) and plan.unchanged == 0
    apply_export(files, target, plan)
    assert (tmp_path / "out" / "posts" / "the-call.gmi").is_file()
    # A second export of the same build is a no-op.
    plan2 = plan_export(files, target)
    assert plan2.empty and plan2.unchanged == len(files.files)
    # Remove a post from the build: the next export deletes it and only it.
    stale = tmp_path / "out" / "stray.txt"
    stale.write_text("not ours")
    del files.files["posts/the-call.gmi"]
    del files.manifest["posts/the-call.gmi"]
    plan3 = plan_export(files, target)
    assert plan3.removed == ["posts/the-call.gmi"]
    apply_export(files, target, plan3)
    assert not (tmp_path / "out" / "posts" / "the-call.gmi").exists()
    assert stale.is_file(), "files the tool did not write are never deleted"
    # A file deleted behind our back is re-sent.
    (tmp_path / "out" / "index.gmi").unlink()
    plan4 = plan_export(files, target)
    assert plan4.added == ["index.gmi"]


def test_agate_layout_nests_under_hostname_with_meta(tmp_path: Path) -> None:
    site = load_site(DEMO)
    files = layout_files(_build(), "gemini", "agate", site)
    assert "content/demo.example.org/index.gmi" in files.files
    meta = files.files["content/demo.example.org/.meta"][0].decode()
    assert "feed.xml: application/atom+xml" in meta
    assert "index.gmi" not in meta


def test_export_cli_dry_run(tmp_path: Path, capsys) -> None:
    from constellation.cli import main

    rc = main(
        [
            "export",
            str(DEMO),
            "--platform",
            "web",
            "--target",
            f"folder:{tmp_path / 'web'}",
            "--now",
            "2026-11-09T00:00:00+00:00",
            "--dry-run",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "added" in out and "+ index.html" in out
    assert not (tmp_path / "web" / "index.html").exists()
    rc = main(
        [
            "export",
            str(DEMO),
            "--platform",
            "web",
            "--target",
            f"folder:{tmp_path / 'web'}",
            "--now",
            "2026-11-09T00:00:00+00:00",
            "--yes",
        ]
    )
    assert rc == 0 and (tmp_path / "web" / "index.html").is_file()
