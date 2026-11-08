"""The release store: content-addressed blobs, immutable releases, an atomic ``current``
pointer per site, retention, rollback, and per-site publish tokens.

Layout under ``<data>/sites/<site>/``: ``blobs/<sha256>``, ``releases/<id>/manifest.json``
plus ``<platform>/...`` trees (hard links into blobs when the filesystem allows, copies
otherwise), ``current`` (a small file naming the active release id, replaced atomically),
``tokens.json`` and ``site.json`` (the server-facing config of the active release).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

SITE_ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SAFE_PATH_RX = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%\-/]+$")
_ph = PasswordHasher()


class ReleaseError(Exception):
    pass


def release_id_for(manifest: dict[str, Any]) -> str:
    """Hash of the content and site config only, never of build time or generator."""
    payload = json.dumps(
        {
            "platforms": manifest.get("platforms", {}),
            "site_config": manifest.get("site_config", {}),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def validate_manifest(manifest: dict[str, Any], *, max_bytes: int) -> None:
    if manifest.get("version") != 1:
        raise ReleaseError("unsupported manifest version")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ReleaseError("manifest has no platforms")
    total = 0
    for name, files in platforms.items():
        if name not in ("web", "gemini", "gopher"):
            raise ReleaseError(f"unknown platform {name!r}")
        if not isinstance(files, dict):
            raise ReleaseError(f"platform {name} is not a mapping")
        for path, entry in files.items():
            if (
                not SAFE_PATH_RX.match(path)
                or path.startswith("/")
                or ".." in path.split("/")
                or "\\" in path
            ):
                raise ReleaseError(f"unsafe path {path!r}")
            sha = entry.get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", str(sha)):
                raise ReleaseError(f"bad sha256 for {path}")
            size = int(entry.get("size", -1))
            if size < 0:
                raise ReleaseError(f"bad size for {path}")
            total += size
    if total > max_bytes:
        raise ReleaseError("release exceeds the configured size limit")
    cfg = manifest.get("site_config", {})
    if not isinstance(cfg, dict):
        raise ReleaseError("site_config must be a mapping")
    for platform, hosts in cfg.get("hostnames", {}).items():
        if not isinstance(hosts, list):
            raise ReleaseError("hostnames must be lists")
        for h in hosts:
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", str(h).lower()):
                raise ReleaseError(f"bad hostname {h!r} for {platform}")


@dataclass(slots=True)
class ReleaseInfo:
    id: str
    created: float
    active: bool
    built_at: str
    generator: str
    files: int


class SiteStore:
    def __init__(self, root: Path, site_id: str) -> None:
        if not SITE_ID_RX.match(site_id):
            raise ReleaseError(f"bad site id {site_id!r}")
        self.site_id = site_id
        self.root = Path(root) / "sites" / site_id
        self.blobs = self.root / "blobs"
        self.releases = self.root / "releases"
        self._lock = threading.Lock()

    def ensure(self) -> None:
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.releases.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.root.is_dir()

    # ---- blobs ------------------------------------------------------------------------------
    def has_blob(self, sha: str) -> bool:
        return (self.blobs / sha).is_file()

    def put_blob(self, sha: str, data: bytes) -> None:
        if hashlib.sha256(data).hexdigest() != sha:
            raise ReleaseError("blob hash mismatch")
        self.ensure()
        tmp = self.blobs / f".{sha}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.blobs / sha)

    # ---- releases ---------------------------------------------------------------------------
    def stage(self, manifest: dict[str, Any], *, max_bytes: int) -> tuple[str, list[str]]:
        """Record a pending release; return its id and the blobs still missing."""
        validate_manifest(manifest, max_bytes=max_bytes)
        self.ensure()
        rid = release_id_for(manifest)
        rdir = self.releases / rid
        if not (rdir / "manifest.json").is_file():
            rdir.mkdir(parents=True, exist_ok=True)
            manifest = dict(manifest)
            manifest["release_id"] = rid
            manifest["staged_at"] = time.time()
            (rdir / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
        missing = sorted(
            {
                e["sha256"]
                for files in manifest["platforms"].values()
                for e in files.values()
                if not self.has_blob(e["sha256"])
            }
        )
        return rid, missing

    def manifest(self, rid: str) -> dict[str, Any]:
        p = self.releases / rid / "manifest.json"
        if not p.is_file():
            raise ReleaseError(f"no release {rid}")
        return dict(json.loads(p.read_text(encoding="utf-8")))

    def commit(self, rid: str) -> dict[str, Any]:
        """Materialise the trees from blobs and switch ``current`` atomically."""
        with self._lock:
            manifest = self.manifest(rid)
            rdir = self.releases / rid
            for platform, files in manifest["platforms"].items():
                for path, entry in files.items():
                    src = self.blobs / entry["sha256"]
                    if not src.is_file():
                        raise ReleaseError(f"missing blob for {path}")
                    dest = rdir / platform / path
                    if dest.exists():
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(src, dest)
                    except OSError:
                        shutil.copyfile(src, dest)
            manifest["committed_at"] = time.time()
            (rdir / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
            self._write_current(rid)
            site_json = {
                "site": self.site_id,
                "release": rid,
                "site_config": manifest.get("site_config", {}),
                "platforms": list(manifest["platforms"]),
            }
            _atomic_json(self.root / "site.json", site_json)
            return manifest

    def activate(self, rid: str) -> dict[str, Any]:
        with self._lock:
            manifest = self.manifest(rid)
            if "committed_at" not in manifest:
                raise ReleaseError("release was never committed")
            self._write_current(rid)
            _atomic_json(
                self.root / "site.json",
                {
                    "site": self.site_id,
                    "release": rid,
                    "site_config": manifest.get("site_config", {}),
                    "platforms": list(manifest["platforms"]),
                },
            )
            return manifest

    def _write_current(self, rid: str) -> None:
        tmp = self.root / ".current.tmp"
        tmp.write_text(rid, encoding="utf-8")
        os.replace(tmp, self.root / "current")

    def current(self) -> str | None:
        p = self.root / "current"
        if not p.is_file():
            return None
        rid = p.read_text(encoding="utf-8").strip()
        return rid or None

    def current_manifest(self) -> dict[str, Any] | None:
        rid = self.current()
        return self.manifest(rid) if rid else None

    def current_dir(self, platform: str) -> Path | None:
        rid = self.current()
        if not rid:
            return None
        d = self.releases / rid / platform
        return d if d.is_dir() else None

    def site_config(self) -> dict[str, Any]:
        p = self.root / "site.json"
        if not p.is_file():
            return {}
        return dict(json.loads(p.read_text(encoding="utf-8")).get("site_config", {}))

    def list_releases(self) -> list[ReleaseInfo]:
        out: list[ReleaseInfo] = []
        cur = self.current()
        if not self.releases.is_dir():
            return out
        for d in self.releases.iterdir():
            mp = d / "manifest.json"
            if not mp.is_file():
                continue
            m = json.loads(mp.read_text(encoding="utf-8"))
            if "committed_at" not in m:
                continue
            out.append(
                ReleaseInfo(
                    d.name,
                    float(m.get("committed_at", 0)),
                    d.name == cur,
                    str(m.get("built_at", "")),
                    str(m.get("generator", "")),
                    sum(len(f) for f in m["platforms"].values()),
                )
            )
        return sorted(out, key=lambda r: r.created, reverse=True)

    def prune(self, keep: int) -> list[str]:
        """Drop the oldest committed releases beyond ``keep`` (never the active one) and orphaned blobs."""
        with self._lock:
            removed: list[str] = []
            infos = self.list_releases()
            for info in infos[keep:]:
                if info.active:
                    continue
                shutil.rmtree(self.releases / info.id, ignore_errors=True)
                removed.append(info.id)
            # Also drop stale, never-committed stagings older than a day.
            now = time.time()
            for d in self.releases.iterdir():
                mp = d / "manifest.json"
                if mp.is_file():
                    m = json.loads(mp.read_text(encoding="utf-8"))
                    if "committed_at" not in m and now - float(m.get("staged_at", now)) > 86400:
                        shutil.rmtree(d, ignore_errors=True)
            referenced: set[str] = set()
            for d in self.releases.iterdir():
                mp = d / "manifest.json"
                if mp.is_file():
                    m = json.loads(mp.read_text(encoding="utf-8"))
                    for files in m["platforms"].values():
                        referenced.update(e["sha256"] for e in files.values())
            if self.blobs.is_dir():
                for b in self.blobs.iterdir():
                    if b.is_file() and not b.name.startswith(".") and b.name not in referenced:
                        b.unlink(missing_ok=True)
            return removed

    # ---- tokens -----------------------------------------------------------------------------
    def _tokens(self) -> list[dict[str, Any]]:
        p = self.root / "tokens.json"
        if not p.is_file():
            return []
        return list(json.loads(p.read_text(encoding="utf-8")).get("tokens", []))

    def mint_token(self, label: str) -> tuple[str, str]:
        self.ensure()
        raw = "cst_" + secrets.token_urlsafe(32)
        tid = secrets.token_hex(4)
        tokens = self._tokens()
        tokens.append(
            {"id": tid, "label": label[:80], "hash": _ph.hash(raw), "created": time.time()}
        )
        _atomic_json(self.root / "tokens.json", {"tokens": tokens})
        return tid, raw

    def revoke_token(self, tid: str) -> bool:
        tokens = self._tokens()
        kept = [t for t in tokens if t["id"] != tid]
        _atomic_json(self.root / "tokens.json", {"tokens": kept})
        return len(kept) != len(tokens)

    def list_tokens(self) -> list[dict[str, Any]]:
        return [
            {"id": t["id"], "label": t["label"], "created": t["created"]} for t in self._tokens()
        ]

    def verify_token(self, raw: str) -> bool:
        for t in self._tokens():
            try:
                if _ph.verify(t["hash"], raw):
                    return True
            except (VerifyMismatchError, ValueError):
                continue
        return False

    def retire(self) -> None:
        """Stop serving: forget the current pointer and hostnames; keep the data on disk."""
        with self._lock:
            (self.root / "current").unlink(missing_ok=True)
            _atomic_json(
                self.root / "site.json",
                {"site": self.site_id, "release": None, "site_config": {}, "platforms": []},
            )


class Store:
    """All sites on this server."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def site(self, site_id: str) -> SiteStore:
        return SiteStore(self.data_dir, site_id)

    def sites(self) -> list[str]:
        d = self.data_dir / "sites"
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and SITE_ID_RX.match(p.name))

    def hostname_table(self) -> dict[str, dict[str, str]]:
        """platform → hostname (lowercase) → site id, from every site's active config."""
        table: dict[str, dict[str, str]] = {"web": {}, "gemini": {}, "gopher": {}}
        for sid in self.sites():
            cfg = self.site(sid).site_config()
            for platform, hosts in cfg.get("hostnames", {}).items():
                for h in hosts:
                    table.setdefault(platform, {})[str(h).lower()] = sid
        return table


def _atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def hash_admin_token(raw: str) -> str:
    return _ph.hash(raw)


def verify_admin_token(hashed: str, raw: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except (VerifyMismatchError, ValueError):
        return False
