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


_LABEL_RX = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_HEADER_NAME_RX = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
_LANG_RX = re.compile(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$")
PLATFORM_NAMES = ("web", "gemini", "gopher")


def valid_hostname(host: object) -> bool:
    """A DNS name made of real labels: no empty labels, no ``..``, at most 253 characters."""
    if not isinstance(host, str):
        return False
    h = host.lower().rstrip(".")
    return 0 < len(h) <= 253 and all(_LABEL_RX.match(label) for label in h.split("."))


def _check_site_config(cfg: object) -> None:
    """The server-facing part of a release. Every field is checked, because a bad value
    would otherwise surface only when the server reloads its routing tables."""
    if not isinstance(cfg, dict):
        raise ReleaseError("site_config must be a mapping")
    hostnames = cfg.get("hostnames", {})
    if not isinstance(hostnames, dict):
        raise ReleaseError("site_config.hostnames must be a mapping")
    for platform, hosts in hostnames.items():
        if platform not in PLATFORM_NAMES or not isinstance(hosts, list):
            raise ReleaseError("site_config.hostnames maps a platform to a list")
        for h in hosts:
            if not valid_hostname(h):
                raise ReleaseError(f"bad hostname {h!r} for {platform}")
    canonical = cfg.get("canonical", {})
    if not isinstance(canonical, dict):
        raise ReleaseError("site_config.canonical must be a mapping")
    for platform, host in canonical.items():
        if platform not in PLATFORM_NAMES or not valid_hostname(host):
            raise ReleaseError(f"bad canonical hostname for {platform}")
    language = cfg.get("language", "en")
    if not isinstance(language, str) or not _LANG_RX.match(language):
        raise ReleaseError("site_config.language must be a language tag such as en or en-US")
    redirects = cfg.get("redirects", [])
    if not isinstance(redirects, list):
        raise ReleaseError("site_config.redirects must be a list")
    for r in redirects:
        if (
            not isinstance(r, dict)
            or not isinstance(r.get("from"), str)
            or not isinstance(r.get("to"), str)
            or not r["from"].startswith("/")
            or int(r.get("status", 301)) not in (301, 302, 307, 308)
            or any(c in r["to"] for c in "\r\n")
        ):
            raise ReleaseError(f"bad redirect {r!r}")
    headers = cfg.get("headers", {})
    if not isinstance(headers, dict):
        raise ReleaseError("site_config.headers must be a mapping")
    for platform, values in headers.items():
        if platform not in PLATFORM_NAMES or not isinstance(values, dict):
            raise ReleaseError("site_config.headers maps a platform to a mapping")
        for k, v in values.items():
            if (
                not _HEADER_NAME_RX.match(str(k))
                or not isinstance(v, str)
                or any(c in v for c in "\r\n\0")
            ):
                raise ReleaseError(f"bad header {k!r}")
    analytics = cfg.get("analytics", {})
    if not isinstance(analytics, dict):
        raise ReleaseError("site_config.analytics must be a mapping")


def validate_manifest(manifest: dict[str, Any], *, max_bytes: int) -> None:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ReleaseError("unsupported manifest version")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ReleaseError("manifest has no platforms")
    total = 0
    for name, files in platforms.items():
        if name not in PLATFORM_NAMES:
            raise ReleaseError(f"unknown platform {name!r}")
        if not isinstance(files, dict):
            raise ReleaseError(f"platform {name} is not a mapping")
        for path, entry in files.items():
            if (
                not isinstance(path, str)
                or not SAFE_PATH_RX.match(path)
                or path.startswith("/")
                or path.endswith("/")
                or "" in path.split("/")
                or ".." in path.split("/")
                or "." in path.split("/")
                or "\\" in path
            ):
                raise ReleaseError(f"unsafe path {path!r}")
            if not isinstance(entry, dict):
                raise ReleaseError(f"bad entry for {path}")
            sha = entry.get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", str(sha)):
                raise ReleaseError(f"bad sha256 for {path}")
            size = entry.get("size", -1)
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ReleaseError(f"bad size for {path}")
            total += size
        # A file and a folder of the same name cannot both exist on disk.
        paths = set(files)
        for path in paths:
            parts = path.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in paths:
                    raise ReleaseError(f"{path!r} needs {'/'.join(parts[:i])!r} to be a folder")
    if total > max_bytes:
        raise ReleaseError("release exceeds the configured size limit")
    _check_site_config(manifest.get("site_config", {}))


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
            self._materialise(rid, manifest)
            manifest["committed_at"] = time.time()
            (self.releases / rid / "manifest.json").write_text(
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

    def _materialise(self, rid: str, manifest: dict[str, Any]) -> None:
        """Build a release's file trees from the blob store (hard links where possible)."""
        rdir = self.releases / rid
        for platform, files in manifest["platforms"].items():
            for path, entry in files.items():
                src = self.blobs / entry["sha256"]
                if not src.is_file():
                    raise ReleaseError(f"missing blob for {path}")
                dest = rdir / platform / path
                if dest.exists():
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(src, dest)
                    except OSError:
                        shutil.copyfile(src, dest)
                except OSError as e:
                    raise ReleaseError(f"cannot write {platform}/{path}: {e}") from e

    def rebuild_trees(self) -> None:
        """Recreate the trees of every committed release, for example after a restore.
        Commit times, the active release and staged-but-uncommitted releases are untouched."""
        with self._lock:
            for d in sorted(self.releases.iterdir()) if self.releases.is_dir() else []:
                if not (d / "manifest.json").is_file():
                    continue
                manifest = self.manifest(d.name)
                if "committed_at" in manifest:
                    self._materialise(d.name, manifest)

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
        """Stop serving: forget the current pointer and hostnames, and revoke every publish
        token so the site cannot come back without the admin minting a new one. The release
        data stays on disk."""
        with self._lock:
            (self.root / "current").unlink(missing_ok=True)
            _atomic_json(
                self.root / "site.json",
                {
                    "site": self.site_id,
                    "release": None,
                    "site_config": {},
                    "platforms": [],
                    "retired": True,
                },
            )
            if (self.root / "tokens.json").exists():
                _atomic_json(self.root / "tokens.json", {"tokens": []})

    def retired(self) -> bool:
        try:
            data = json.loads((self.root / "site.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(data.get("retired"))


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
        """platform → hostname (lowercase) → site id, from every site's active config.

        Publishing refuses a hostname another site already serves, so a name normally has
        one owner. If two sites still claim one (data from before that check), the site that
        committed first keeps it.
        """
        table: dict[str, dict[str, str]] = {"web": {}, "gemini": {}, "gopher": {}}
        owners: list[tuple[float, str]] = []
        for sid in self.sites():
            site = self.site(sid)
            m = site.current_manifest() or {}
            owners.append((float(m.get("committed_at", 0) or 0), sid))
        for _when, sid in sorted(owners):
            cfg = self.site(sid).site_config()
            hostnames = cfg.get("hostnames", {})
            if not isinstance(hostnames, dict):
                continue
            for platform, hosts in hostnames.items():
                if not isinstance(hosts, list):
                    continue
                for h in hosts:
                    table.setdefault(platform, {}).setdefault(str(h).lower(), sid)
        return table

    def hostname_conflicts(self, site_id: str, site_config: dict[str, Any]) -> list[str]:
        """Hostnames in ``site_config`` that another site already serves."""
        table = self.hostname_table()
        clashes: list[str] = []
        hostnames = site_config.get("hostnames", {})
        for platform, hosts in hostnames.items() if isinstance(hostnames, dict) else []:
            for h in hosts if isinstance(hosts, list) else []:
                owner = table.get(platform, {}).get(str(h).lower())
                if owner and owner != site_id:
                    clashes.append(f"{h} ({platform}, served by {owner})")
        return clashes


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
