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
# Uploads are published as-is; HTML, SVG and scripts could run on the site's origin.
MEDIA_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".pdf",
        ".txt",
        ".mp3",
        ".ogg",
        ".opus",
        ".flac",
        ".wav",
        ".mp4",
        ".webm",
        ".zip",
        ".epub",
    }
)
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
    doc = _read_meta(raw, rel)
    bundle = path.parent if path.name == "index.md" else None
    media = (
        sorted(
            f.relative_to(bundle).as_posix()
            for f in bundle.rglob("*")
            if f.is_file()
            and f.relative_to(bundle).as_posix() != "index.md"
            and not any(part.startswith(".") for part in f.relative_to(bundle).parts)
        )
        if bundle
        else []
    )
    kind = _kind_of(site_root, path)
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
    base = slugify(title) or "untitled"
    if base in RESERVED:
        base = f"{base}-page"
    taken = existing_slugs(site_root, kind)
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    if kind == "post":
        date = date or datetime.now().astimezone()
        folder = site_root / "content" / "posts" / f"{date:%Y-%m-%d}-{slug}"
        while folder.exists():
            slug = f"{base}-{n}"
            folder = site_root / "content" / "posts" / f"{date:%Y-%m-%d}-{slug}"
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
    if loaded.kind == "post":
        # The copy is a new post dated today, matching the date in its folder name.
        fresh = frontmatter.loads(new_path.read_text(encoding="utf-8")).metadata
        meta["date"] = fresh.get("date", meta.get("date"))
        meta.pop("updated", None)
    atomic_write(new_path, compose(meta, loaded.body))
    if loaded.bundle is not None and new_path.name == "index.md":
        for name in loaded.media:
            dest = new_path.parent / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(loaded.bundle / name, dest)
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
    if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError(
            "that kind of file cannot be uploaded; allowed: "
            + ", ".join(sorted(e.lstrip(".") for e in MEDIA_EXTENSIONS))
        )
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
    """Resolve ``rel`` to a Markdown post or page inside the site's content folders."""
    root = site_root.resolve()
    path = (root / rel.replace("\\", "/")).resolve()
    if root not in path.parents:
        raise ValueError("path escapes the site")
    if "_trash" in path.parts:
        raise ValueError("trashed items are read-only")
    inner = path.relative_to(root).parts
    if path.suffix != ".md" or inner[:2] not in (("content", "posts"), ("content", "pages")):
        raise ValueError("not a post or page")
    return path


def _kind_of(site_root: Path, path: Path) -> str:
    return "post" if path.resolve().relative_to(site_root.resolve()).parts[1] == "posts" else "page"


def _read_meta(raw: bytes, rel: str) -> frontmatter.Post:
    try:
        return frontmatter.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ValueError(f"{rel} is not UTF-8 text; save it as UTF-8") from None
    except yaml.YAMLError as e:
        detail = " ".join(str(e).split())
        raise ValueError(
            f"{rel}: the front matter is not valid YAML ({detail}). "
            "Fix it in a text editor, then reload."
        ) from None


DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def existing_slugs(site_root: Path, kind: str) -> set[str]:
    """Slugs already used by posts or pages, from names and ``slug:`` front matter."""
    folder = site_root / "content" / ("posts" if kind == "post" else "pages")
    slugs: set[str] = set()
    if not folder.is_dir():
        return slugs
    for entry in folder.iterdir():
        if entry.name.startswith((".", "_")):
            continue
        source = entry / "index.md" if entry.is_dir() else entry
        if source.suffix != ".md" or not source.is_file():
            continue
        name = entry.name if entry.is_dir() else entry.stem
        slugs.add(DATED.sub("", name) if kind == "post" else name)
        try:
            meta = frontmatter.loads(source.read_text(encoding="utf-8-sig")).metadata
        except Exception:  # noqa: BLE001 - unreadable files still reserve their name
            continue
        slug = meta.get("slug")
        if isinstance(slug, str):
            slugs.add(slug)
    return slugs
