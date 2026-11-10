"""`constellation import publii-backup`: drafts and other items from Publii's database."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
import pytest

from constellation.cli import main
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.importers.html_to_md import html_to_markdown
from constellation.importers.publii_backup import BackupImportError, merge_publii_backup

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
MS = 1792694630000  # 2026-10-22 in milliseconds, as Publii stores dates

SCHEMA = """
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, username TEXT, password TEXT, config TEXT, additional_data TEXT);
CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT, authors TEXT, slug TEXT, text TEXT, featured_image_id INTEGER,
  created_at DATETIME, modified_at DATETIME, status TEXT, template TEXT);
CREATE TABLE posts_additional_data (id INTEGER PRIMARY KEY, post_id INTEGER, key TEXT, value TEXT);
CREATE TABLE posts_images (id INTEGER PRIMARY KEY, post_id INTEGER, url TEXT, title TEXT, caption TEXT, additional_data TEXT);
CREATE TABLE posts_tags (tag_id INTEGER NOT NULL, post_id INTEGER NOT NULL, PRIMARY KEY (tag_id, post_id));
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, slug TEXT, description TEXT, additional_data TEXT);
"""


def _backup(tmp: Path) -> Path:
    """A Publii site folder like the one inside a backup .tar."""
    base = tmp / "publii" / "input"
    (base / "media" / "posts" / "20").mkdir(parents=True)
    (base / "media" / "posts" / "20" / "photo.png").write_bytes(b"\x89PNG fake")
    (base / "media" / "posts" / "20" / "cover.jpg").write_bytes(b"jpg")
    db = sqlite3.connect(base / "db.sqlite")
    db.executescript(SCHEMA)
    db.execute("insert into authors values (1, 'Demo Author', 'demo', '', '{}', '')")
    db.executemany(
        "insert into tags values (?, ?, ?, ?, '{}')",
        [
            (1, "Foundations", "foundations", "Existing tag"),
            (2, "Parables & Letters", "parables-and-letters", "Stories."),
        ],
    )
    posts = [
        # id, title, slug, text, image id, status, editor
        (1, "The Call", "the-call", "Already in the site", None, "published,featured", "markdown"),
        (
            20,
            "A Letter",
            "a-letter",
            "See [the call](#INTERNAL_LINK#/post/1) and [the hearth](#INTERNAL_LINK#/page/30#rules).\n\n"
            "![A photo](#DOMAIN_NAME#photo.png)\n\nAlso [a ghost](#INTERNAL_LINK#/post/999).",
            7,
            "draft",
            "markdown",
        ),
        (
            21,
            "Old Style",
            "old-style",
            "<p>An <em> early</em> post with <strong>HTML</strong>.</p>",
            None,
            "draft",
            "tinymce",
        ),
        (
            30,
            "The Hearth",
            "the-hearth",
            "## Rules\n\nKeep it warm.",
            None,
            "draft,is-page",
            "markdown",
        ),
        (31, "Thrown Away", "thrown-away", "Gone.", None, "draft,is-page,trashed", "markdown"),
        (
            32,
            "Live Page",
            "live-page",
            "Published but missing here.",
            None,
            "published,is-page",
            "markdown",
        ),
    ]
    for pid, title, slug, text, img, status, editor in posts:
        db.execute(
            "insert into posts values (?, ?, '1', ?, ?, ?, ?, ?, ?, '')",
            (pid, title, slug, text, img, MS, MS + 3_600_000, status),
        )
        core = {
            "metaTitle": "",
            "metaDesc": f"About {title}",
            "metaRobots": "index, follow",
            "canonicalUrl": "",
            "editor": editor,
        }
        if pid == 20:
            core["mainTag"] = 2
        db.execute(
            "insert into posts_additional_data (post_id, key, value) values (?, '_core', ?)",
            (pid, json.dumps(core)),
        )
    db.execute(
        "insert into posts_images values (7, 20, 'cover.jpg', '', '', ?)",
        (json.dumps({"alt": "The cover"}),),
    )
    db.executemany("insert into posts_tags values (?, ?)", [(1, 20), (2, 20)])
    db.commit()
    db.close()
    return base.parent


def _site(tmp: Path) -> Path:
    root = tmp / "site"
    shutil.copytree(DEMO, root)
    return root


def test_adds_missing_items_keeping_drafts_as_drafts(tmp_path: Path) -> None:
    site = _site(tmp_path)
    before = build_site(load_site(site), now=NOW).platforms["web"].manifest()
    report = merge_publii_backup(_backup(tmp_path), site)
    added = {a.slug: a for a in report.added}
    assert set(added) == {"a-letter", "old-style", "the-hearth", "live-page"}
    assert [a.status for a in (added["a-letter"], added["old-style"], added["the-hearth"])] == [
        "draft"
    ] * 3
    assert added["live-page"].status == "published"
    assert report.already == ["post 'the-call' (The Call)"]
    assert report.trashed == ["page 'thrown-away' (Thrown Away)"]

    letter = frontmatter.load(site / "content" / "posts" / "2026-10-22-a-letter" / "index.md")
    assert letter["status"] == "draft"
    assert letter["date"].startswith("2026-10-22T")
    assert letter["tags"] == ["Parables & Letters", "Foundations"]  # Publii's main tag first
    assert letter["seo"] == {"description": "About A Letter"}
    assert letter["image"] == "cover.jpg" and letter["image_alt"] == "The cover"
    assert "[the call](post:the-call)" in letter.content
    assert "[the hearth](page:the-hearth#rules)" in letter.content
    assert "![A photo](photo.png)" in letter.content
    assert "#INTERNAL_LINK#/post/999" in letter.content  # nothing to point at: left for a human
    assert any("post 999" in w for w in report.warnings)
    assert (
        site / "content" / "posts" / "2026-10-22-a-letter" / "photo.png"
    ).read_bytes() == b"\x89PNG fake"
    assert report.media == 2

    old = frontmatter.load(site / "content" / "posts" / "2026-10-22-old-style" / "index.md")
    assert old.content.strip() == "An *early* post with **HTML**."
    assert (site / "content" / "pages" / "the-hearth.md").is_file()
    assert report.tags == ["Parables & Letters"]  # Foundations already had a file
    assert (
        frontmatter.load(site / "content" / "tags" / "parables-and-letters.md")["description"]
        == "Stories."
    )

    # Drafts never reach the published site; only the missing published page does.
    s = load_site(site)
    after = build_site(s, now=NOW).platforms["web"].manifest()
    new = set(after) - set(before)
    assert any(p.startswith("live-page/") for p in new)
    assert not any("a-letter" in p or "old-style" in p or "the-hearth" in p for p in new)
    preview = build_site(s, now=NOW, include_drafts=True).platforms["web"].files
    assert any("a-letter" in p for p in preview)


def test_running_twice_adds_nothing_more(tmp_path: Path) -> None:
    site = _site(tmp_path)
    backup = _backup(tmp_path)
    merge_publii_backup(backup, site)
    again = merge_publii_backup(backup, site)
    assert again.added == [] and len(again.already) == 5


def test_trash_and_dry_run(tmp_path: Path) -> None:
    site = _site(tmp_path)
    backup = _backup(tmp_path)
    dry = merge_publii_backup(backup, site, include_trashed=True, dry_run=True)
    assert {a.slug for a in dry.added} >= {"thrown-away"}
    assert not (site / "content" / "pages" / "thrown-away.md").exists()
    merge_publii_backup(backup, site, include_trashed=True)
    assert frontmatter.load(site / "content" / "pages" / "thrown-away.md")["status"] == "draft"


def test_reads_the_backup_tar_and_ignores_unsafe_members(tmp_path: Path) -> None:
    folder = _backup(tmp_path)
    tar_path = tmp_path / "site-backup.tar"
    with tarfile.open(tar_path, "w") as tar:
        tar.add(folder / "input", arcname="input")
        for name in ("../escape.txt", "input/media/posts/../../../../escape2.txt", "/abs.txt"):
            info = tarfile.TarInfo(name)
            info.size = 3
            tar.addfile(info, io.BytesIO(b"bad"))
    site = _site(tmp_path)
    assert main(["import", "publii-backup", str(tar_path), str(site)]) == 0
    assert (site / "content" / "posts" / "2026-10-22-a-letter" / "index.md").is_file()
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "escape2.txt").exists()


def test_refuses_things_that_are_not_backups(tmp_path: Path) -> None:
    with pytest.raises(BackupImportError, match="not a Publii backup"):
        merge_publii_backup(tmp_path, _site(tmp_path))
    with pytest.raises(BackupImportError, match="not a ConstellationCMS site"):
        merge_publii_backup(_backup(tmp_path), tmp_path / "nowhere")


def test_emphasis_with_a_leading_space_still_works() -> None:
    # Publii wrote "this:<em> a mended thing</em>"; "* a mended thing*" is not emphasis in Markdown.
    assert (
        html_to_markdown("<p>this:<em> a mended thing.</em></p>").strip()
        == "this: *a mended thing.*"
    )
    assert html_to_markdown("<p>x<strong> y</strong> z</p>").strip() == "x **y** z"
