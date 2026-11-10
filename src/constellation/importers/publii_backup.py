"""Bring the posts and pages from a Publii backup into an existing ConstellationCMS site.

The folder Publii publishes holds only what is published. Publii's own backup (the ``.tar``
from *Backups* in the app, or the site folder itself) also holds drafts, in its database
``input/db.sqlite``. This adds every post and page the site does not have yet (matched by slug),
keeping its status, so drafts stay drafts:

    constellation import publii-backup site-backup.tar sites/my-site

Nothing already in the site is changed. Items in Publii's trash are left out unless
``--include-trashed`` is given, and then come in as drafts.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

import frontmatter

from constellation.importers.html_to_md import html_to_markdown
from constellation.importers.publii import _front_matter

INTERNAL = re.compile(r"#INTERNAL_LINK#/(post|page|tag|author)/(\d+)(#[\w-]*)?/?")
DOMAIN = "#DOMAIN_NAME#"
WANTED = re.compile(
    r"^input/(db\.sqlite|config/[^/]+\.json|media/(posts|website|authors|tags)/.+)$"
)


class BackupImportError(Exception):
    pass


@dataclass(slots=True)
class Added:
    kind: str
    slug: str
    title: str
    status: str
    path: str


@dataclass(slots=True)
class BackupReport:
    added: list[Added] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    trashed: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    media: int = 0
    warnings: list[str] = field(default_factory=list)


def _extract(tar_path: Path, into: Path) -> Path:
    """Unpack only the database, config and media, refusing anything that escapes ``into``."""
    with tarfile.open(tar_path) as tar:
        for m in tar.getmembers():
            name = m.name.lstrip("./")
            if not WANTED.match(name):
                continue
            parts = PurePosixPath(name).parts
            if ".." in parts or name.startswith("/") or not (m.isfile() or m.isdir()):
                continue
            target = into.joinpath(*parts)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    return into / "input"


def _input_dir(src: Path, tmp: Path) -> Path:
    if src.is_file() and tarfile.is_tarfile(src):
        base = _extract(src, tmp)
    elif (src / "input" / "db.sqlite").is_file():
        base = src / "input"
    elif (src / "db.sqlite").is_file():
        base = src
    else:
        raise BackupImportError(
            f"{src} is not a Publii backup: expected a .tar from Publii's Backups, or a site "
            "folder with input/db.sqlite"
        )
    if not (base / "db.sqlite").is_file():
        raise BackupImportError(f"no db.sqlite in {src}")
    return base


def _when(ms: Any, tz: ZoneInfo) -> str:
    try:
        stamp = datetime.fromtimestamp(int(ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return stamp.astimezone(tz).replace(microsecond=0).isoformat()


def _json(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _existing(site_root: Path) -> set[str]:
    """Slugs of every post and page already in the site, drafts included."""
    slugs: set[str] = set()
    for kind in ("posts", "pages"):
        folder = site_root / "content" / kind
        if not folder.is_dir():
            continue
        for f in folder.rglob("*.md"):
            try:
                meta = frontmatter.load(f).metadata
            except Exception:  # noqa: BLE001 - a broken file still claims its name
                meta = {}
            slug = str(meta.get("slug") or "")
            if not slug:
                stem = f.parent.name if f.name == "index.md" else f.stem
                slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
            slugs.add(slug)
    return slugs


def merge_publii_backup(
    src: Path, site_root: Path, *, include_trashed: bool = False, dry_run: bool = False
) -> BackupReport:
    """Add the posts and pages in a Publii backup that ``site_root`` does not have yet."""
    from constellation.core.content import load_config

    site_root = Path(site_root)
    if not (site_root / "site.toml").is_file():
        raise BackupImportError(f"{site_root} is not a ConstellationCMS site (no site.toml)")
    cfg = load_config(site_root)
    try:
        tz = ZoneInfo(cfg.site.timezone or "UTC")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    report = BackupReport()
    with tempfile.TemporaryDirectory(prefix="publii-backup-") as tmp:
        base = _input_dir(Path(src), Path(tmp))
        db = sqlite3.connect(f"file:{(base / 'db.sqlite').as_posix()}?mode=ro", uri=True)
        try:
            _merge(
                db,
                base,
                site_root,
                cfg,
                tz,
                report,
                include_trashed=include_trashed,
                dry_run=dry_run,
            )
        finally:
            db.close()
    return report


def _merge(
    db: sqlite3.Connection,
    base: Path,
    site_root: Path,
    cfg: Any,
    tz: ZoneInfo,
    report: BackupReport,
    *,
    include_trashed: bool,
    dry_run: bool,
) -> None:
    rows = db.execute(
        "select id, title, authors, slug, text, featured_image_id, created_at, modified_at, status, template from posts"
    ).fetchall()
    slug_of = {r[0]: (r[3], "page" if "is-page" in (r[8] or "") else "post") for r in rows}
    tags = {
        r[0]: (r[1], r[2], r[3] or "")
        for r in db.execute("select id, name, slug, description from tags")
    }
    post_tags: dict[int, list[int]] = {}
    for tag_id, post_id in db.execute("select tag_id, post_id from posts_tags"):
        post_tags.setdefault(post_id, []).append(tag_id)
    extra: dict[int, dict[str, dict[str, Any]]] = {}
    for post_id, key, value in db.execute("select post_id, key, value from posts_additional_data"):
        extra.setdefault(post_id, {})[key] = _json(value)
    images = {
        r[0]: (r[1], r[2] or "", _json(r[3]))
        for r in db.execute("select id, post_id, url, additional_data from posts_images")
    }
    publii_authors = {r[0]: str(r[1] or "") for r in db.execute("select id, username from authors")}
    known_authors = {a.id for a in cfg.authors}

    def link(m: re.Match[str]) -> str:
        kind, num, frag = m.group(1), int(m.group(2)), m.group(3) or ""
        if kind in ("post", "page") and num in slug_of:
            slug, real_kind = slug_of[num]
            return f"{real_kind}:{slug}{frag}"
        if kind == "tag" and num in tags:
            return f"tag:{tags[num][1]}{frag}"
        if kind == "author" and num in publii_authors:
            return f"author:{publii_authors[num]}{frag}"
        report.warnings.append(f"a link to Publii {kind} {num} points at nothing; left as written")
        return m.group(0)

    existing = _existing(site_root)
    existing_tags = {f.stem for f in (site_root / "content" / "tags").glob("*.md")}
    for pid, title, authors, slug, text, image_id, created, modified, status, template in rows:
        status = status or ""
        flags = set(status.split(","))
        kind = "page" if "is-page" in flags else "post"
        label = f"{kind} {slug!r} ({title})"
        if "trashed" in flags and not include_trashed:
            report.trashed.append(label)
            continue
        if slug in existing:
            report.already.append(label)
            continue
        core = extra.get(pid, {}).get("_core", {})
        editor = str(core.get("editor") or "tinymce")
        body = str(text or "")
        if editor == "markdown":
            body = INTERNAL.sub(link, body).replace(DOMAIN, "")
        elif editor == "blockeditor":
            report.warnings.append(
                f"{label}: written with Publii's block editor; kept as HTML to tidy by hand"
            )
            body = INTERNAL.sub(link, body).replace(DOMAIN, "")
        else:
            body = html_to_markdown(
                INTERNAL.sub(link, body).replace(DOMAIN, ""), lambda href, _img: href
            )
        body = body.strip() + "\n"

        media: dict[str, Path] = {}
        media_dir = base / "media" / "posts" / str(pid)
        for name in sorted(set(re.findall(r"\]\(([^)\s\"']+)", body))):
            if "/" not in name and (media_dir / name).is_file():
                media[name] = media_dir / name
        image = image_alt = ""
        if image_id and image_id in images and images[image_id][1]:
            name = PurePosixPath(images[image_id][1]).name
            if (media_dir / name).is_file():
                image, image_alt = name, str(images[image_id][2].get("alt", ""))
                media[name] = media_dir / name

        date = _when(created, tz)
        front: dict[str, Any] = {"title": title or slug, "slug": slug}
        if kind == "post":
            front["date"] = date
        if "published" in flags and modified and created and int(modified) - int(created) > 60_000:
            front["updated"] = _when(modified, tz)
        published = "published" in flags and "trashed" not in flags
        front["status"] = "published" if published else ("hidden" if "hidden" in flags else "draft")
        if front["status"] == "published":
            del front["status"]
        author_ids = [
            publii_authors.get(int(a)) for a in str(authors or "").split(",") if a.strip().isdigit()
        ]
        author = next((a for a in author_ids if a), "")
        if author and author in known_authors and author != cfg.site.author:
            front["author"] = author
        if kind == "post":
            ids = post_tags.get(pid, [])
            main = core.get("mainTag")
            ordered = sorted(ids, key=lambda t: (t != main, tags.get(t, ("",))[0].lower()))
            front["tags"] = [tags[t][0] for t in ordered if t in tags]
            if "featured" in flags:
                front["featured"] = True
        seo: dict[str, Any] = {}
        if core.get("metaTitle"):
            seo["title"] = core["metaTitle"]
        if core.get("metaDesc"):
            seo["description"] = core["metaDesc"]
        if core.get("canonicalUrl"):
            seo["canonical"] = core["canonicalUrl"]
        if "noindex" in str(core.get("metaRobots", "")):
            seo["noindex"] = True
        front["seo"] = seo
        front["image"], front["image_alt"] = image, image_alt
        if template and template not in ("", "default"):
            front["template"] = template

        if kind == "post":
            folder = site_root / "content" / "posts" / f"{(date or '1970-01-01')[:10]}-{slug}"
            target = folder / "index.md"
        elif media:
            folder = site_root / "content" / "pages" / slug
            target = folder / "index.md"
        else:
            folder = site_root / "content" / "pages"
            target = folder / f"{slug}.md"
        if target.exists():
            report.already.append(label)
            continue
        report.added.append(
            Added(
                kind,
                slug,
                title or slug,
                front.get("status", "published"),
                target.relative_to(site_root).as_posix(),
            )
        )
        existing.add(slug)
        if dry_run:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(_front_matter(front) + body, encoding="utf-8", newline="\n")
        for name, source in media.items():
            shutil.copy2(source, target.parent / name)
            report.media += 1
        for t in post_tags.get(pid, []) if kind == "post" else []:
            if t not in tags:
                continue
            name, t_slug, desc = tags[t]
            if t_slug in existing_tags:
                continue
            existing_tags.add(t_slug)
            report.tags.append(name)
            tag_file = site_root / "content" / "tags" / f"{t_slug}.md"
            tag_file.parent.mkdir(parents=True, exist_ok=True)
            tag_file.write_text(
                _front_matter({"title": name, "description": desc}), encoding="utf-8", newline="\n"
            )
