"""File operations on a site's content: list, load, save (atomically, with change detection),
create, trash, duplicate, and media uploads. The studio never bypasses these."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import frontmatter
import yaml
from slugify import slugify

from constellation.studio.state import State
from constellation.studio.workspace import atomic_write

CONFLICT_PATTERNS = (
    re.compile(r" \(conflicted copy", re.I),  # Dropbox
    re.compile(r"\.sync-conflict-", re.I),  # Syncthing
    re.compile(r"-[A-Za-z0-9]+-\d+\.md$"),  # OneDrive style "file-DESKTOP-1.md"
    re.compile(r"\(\d+\)\.md$"),  # generic copies
)
SAFE_MEDIA = re.compile(r"[^a-z0-9._-]+")
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class ConflictError(Exception):
    """The file changed on disk since it was loaded."""


@dataclass
class Loaded:
    kind: str
    slug: str
    path: Path
    rel: str
    meta: dict[str, Any]
    body: str
    sha256: str
    mtime: float
    bundle: Path | None
    media: list[str] = field(default_factory=list)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel_path(site_root: Path, path: Path) -> str:
    return path.relative_to(site_root).as_posix()


def list_items(site_root: Path, kind: str) -> list[dict[str, Any]]:
    folder = site_root / "content" / ("posts" if kind == "post" else "pages")
    items: list[dict[str, Any]] = []
    if not folder.is_dir():
        return items
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        source = entry / "index.md" if entry.is_dir() else entry
        if not source.is_file() or source.suffix != ".md":
            continue
        try:
            doc = frontmatter.loads(source.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            items.append(
                {
                    "slug": entry.stem,
                    "title": entry.name,
                    "error": str(e),
                    "rel": rel_path(site_root, source),
                }
            )
            continue
        m = doc.metadata
        name = entry.name if entry.is_dir() else entry.stem
        slug = str(m.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name))
        items.append(
            {
                "slug": slug,
                "title": str(m.get("title", slug)),
                "status": str(m.get("status", "published")),
                "date": m.get("date"),
                "tags": [str(t) for t in cast(list[object], m.get("tags") or [])],
                "rel": rel_path(site_root, source),
                "kind": kind,
            }
        )
    if kind == "post":
        items.sort(key=lambda i: (str(i.get("date") or ""), i["slug"]), reverse=True)
    else:
        items.sort(key=lambda i: i["slug"])
    return items


def load_item(
    site_root: Path, rel: str, *, state: State | None = None, site_id: str = ""
) -> Loaded:
    path = _safe(site_root, rel)
    raw = path.read_bytes()
    doc = frontmatter.loads(raw.decode("utf-8"))
    bundle = path.parent if path.name == "index.md" else None
    media = (
        sorted(
            f.name
            for f in bundle.iterdir()
            if f.is_file() and f.name != "index.md" and not f.name.startswith(".")
        )
        if bundle
        else []
    )
    kind = "post" if "/posts/" in path.as_posix() else "page"
    name = bundle.name if bundle else path.stem
    slug = str(doc.metadata.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name))
    loaded = Loaded(
        kind,
        slug,
        path,
        rel,
        dict(doc.metadata),
        doc.content,
        _sha(raw),
        path.stat().st_mtime,
        bundle,
        media,
    )
    if state is not None:
        state.index_set(site_id, rel, loaded.sha256, loaded.mtime)
    return loaded


def compose(meta: dict[str, Any], body: str) -> str:
    """Front matter + body, keys in a stable order."""
    order = [
        "title",
        "slug",
        "date",
        "updated",
        "status",
        "author",
        "tags",
        "categories",
        "series",
        "series_part",
        "summary",
        "image",
        "image_alt",
        "seo",
        "platforms",
        "template",
        "menu_order",
    ]
    ordered: dict[str, Any] = {}
    for k in order:
        if k in meta and meta[k] not in (None, "", [], {}):
            ordered[k] = meta[k]
    for k, v in meta.items():
        if k not in ordered and v not in (None, "", [], {}):
            ordered[k] = v
    fm = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=None
    ).rstrip("\n")
    return f"---\n{fm}\n---\n\n{body.strip()}\n" if ordered else body.strip() + "\n"


def save_item(
    site_root: Path,
    rel: str,
    meta: dict[str, Any],
    body: str,
    *,
    expected_sha: str | None,
    state: State | None = None,
    site_id: str = "",
    force: bool = False,
) -> Loaded:
    path = _safe(site_root, rel)
    if path.exists() and expected_sha is not None and not force:
        current = _sha(path.read_bytes())
        if current != expected_sha:
            raise ConflictError(f"{rel} changed on disk since it was opened")
    text = compose(meta, body)
    atomic_write(path, text)
    return load_item(site_root, rel, state=state, site_id=site_id)


def create_item(
    site_root: Path, kind: str, title: str, *, date: datetime | None = None, status: str = "draft"
) -> str:
    slug = slugify(title) or "untitled"
    if slug in RESERVED:
        slug = f"{slug}-page"
    if kind == "post":
        date = date or datetime.now().astimezone()
        folder = site_root / "content" / "posts" / f"{date:%Y-%m-%d}-{slug}"
        n = 2
        while folder.exists():
            folder = site_root / "content" / "posts" / f"{date:%Y-%m-%d}-{slug}-{n}"
            n += 1
        folder.mkdir(parents=True)
        path = folder / "index.md"
        meta: dict[str, Any] = {
            "title": title,
            "date": date.replace(microsecond=0).isoformat(),
            "status": status,
            "tags": [],
        }
    else:
        path = site_root / "content" / "pages" / f"{slug}.md"
        n = 2
        while path.exists():
            path = site_root / "content" / "pages" / f"{slug}-{n}.md"
            n += 1
        meta = {"title": title}
        if status == "draft":
            meta["status"] = "draft"
    atomic_write(path, compose(meta, ""))
    return rel_path(site_root, path)


def trash_item(site_root: Path, rel: str, *, state: State | None = None, site_id: str = "") -> Path:
    path = _safe(site_root, rel)
    target_root = site_root / "content" / "_trash"
    target_root.mkdir(parents=True, exist_ok=True)
    src = path.parent if path.name == "index.md" else path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = target_root / f"{stamp}-{src.name}"
    shutil.move(str(src), str(dest))
    if state is not None:
        state.index_delete(site_id, rel)
    return dest


def duplicate_item(site_root: Path, rel: str) -> str:
    loaded = load_item(site_root, rel)
    meta = dict(loaded.meta)
    meta["title"] = f"{meta.get('title', loaded.slug)} (copy)"
    meta.pop("slug", None)
    meta["status"] = "draft"
    new_rel = create_item(site_root, loaded.kind, str(meta["title"]))
    new_path = _safe(site_root, new_rel)
    atomic_write(new_path, compose(meta, loaded.body))
    if loaded.bundle is not None and new_path.name == "index.md":
        for name in loaded.media:
            shutil.copyfile(loaded.bundle / name, new_path.parent / name)
    return new_rel


def safe_media_name(name: str) -> str:
    base = Path(name).name.lower()
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = SAFE_MEDIA.sub("-", stem).strip("-.") or "file"
    ext = SAFE_MEDIA.sub("", ext)
    if stem in RESERVED:
        stem += "-file"
    return f"{stem}.{ext}" if ext else stem


def add_media(
    site_root: Path, rel: str, filename: str, data: bytes, *, max_bytes: int = 25 * 1024 * 1024
) -> str:
    """Store an upload next to the item (posts must be bundles). Returns the stored file name."""
    if len(data) > max_bytes:
        raise ValueError("file is too large")
    path = _safe(site_root, rel)
    if path.name != "index.md":
        raise ValueError("this item is a single file; convert it to a bundle first")
    name = safe_media_name(filename)
    dest = path.parent / name
    n = 2
    while dest.exists():
        stem, dot, ext = name.rpartition(".")
        dest = path.parent / (f"{stem}-{n}.{ext}" if dot else f"{name}-{n}")
        n += 1
    if dest.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        try:
            from PIL import Image

            Image.open(io.BytesIO(data)).verify()
        except Exception as e:  # noqa: BLE001
            raise ValueError("the file is not a valid image") from e
    atomic_write(dest, data)
    return dest.name


def convert_to_bundle(site_root: Path, rel: str) -> str:
    path = _safe(site_root, rel)
    if path.name == "index.md":
        return rel
    folder = path.with_suffix("")
    folder.mkdir()
    new = folder / "index.md"
    shutil.move(str(path), str(new))
    return rel_path(site_root, new)


def find_conflict_files(site_root: Path) -> list[str]:
    out: list[str] = []
    content = site_root / "content"
    if not content.is_dir():
        return out
    for f in content.rglob("*"):
        if f.is_file() and any(p.search(f.name) for p in CONFLICT_PATTERNS):
            out.append(rel_path(site_root, f))
    return sorted(out)


def _safe(site_root: Path, rel: str) -> Path:
    root = site_root.resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("path escapes the site")
    if "_trash" in path.parts:
        raise ValueError("trashed items are read-only")
    return path
