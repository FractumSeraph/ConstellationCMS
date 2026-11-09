"""Static export of one platform's build to a folder, over SFTP, or with rsync.

Every target keeps a small manifest (``.constellation-export.json``) at its root so the next
export can compute a diff (added, changed, removed) without hashing remote files, and so that
deletion only ever touches files this tool wrote.
"""

from __future__ import annotations

import contextlib
import json
import os
import posixpath
import shutil
import stat
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from constellation.core.build.pipeline import Build, PlatformBuild, diff_manifests
from constellation.core.models import Site

MANIFEST_NAME = ".constellation-export.json"


@dataclass(slots=True)
class Plan:
    added: list[str]
    changed: list[str]
    removed: list[str]
    unchanged: int

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    def summary(self) -> str:
        return f"{len(self.added)} added, {len(self.changed)} changed, {len(self.removed)} removed, {self.unchanged} unchanged"


@dataclass(slots=True)
class ExportFiles:
    """What to write: path → (bytes or source file)."""

    files: dict[str, tuple[bytes | None, Path | None]] = field(default_factory=dict)
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)


def layout_files(build: Build, platform: str, layout: str, site: Site) -> ExportFiles:
    """Arrange a platform build for a target layout.

    ``plain`` is the release tree. ``agate`` nests the Gemini tree under ``content/<hostname>/``
    and adds a ``.meta`` sidecar so Agate serves feeds with the right media type.
    """
    pb: PlatformBuild = build.platforms[platform]
    out = ExportFiles()
    prefix = ""
    if layout == "agate":
        if platform != "gemini":
            raise ValueError("the agate layout applies to the gemini platform")
        hosts = site.config.platform("gemini").hostnames
        host = (
            hosts[0] if hosts else urlsplit(site.config.platform("gemini").base_url).hostname or ""
        )
        prefix = f"content/{host}/" if host else "content/"
    elif layout != "plain":
        raise ValueError(f"unknown layout {layout!r}")
    for path, f in pb.files.items():
        out.files[prefix + path] = (None if f.source else f.data, f.source)
        out.manifest[prefix + path] = {
            "sha256": f.sha256,
            "size": len(f.bytes()),
            "type": f.media_type,
        }
    if layout == "agate":
        meta_lines = [
            f"{posixpath.basename(p)}: {e['type']}"
            for p, e in sorted(out.manifest.items())
            if posixpath.dirname(p) == prefix.rstrip("/")
            and e["type"] not in ("text/gemini",)
            and not p.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt"))
        ]
        if meta_lines:
            data = ("\n".join(meta_lines) + "\n").encode()
            out.files[prefix + ".meta"] = (data, None)
            import hashlib

            out.manifest[prefix + ".meta"] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "type": "text/plain",
            }
    return out


class Target(ABC):
    """A place files can be written to, listed, and deleted from."""

    @abstractmethod
    def read_manifest(self) -> dict[str, dict[str, Any]] | None: ...

    @abstractmethod
    def write(self, path: str, data: bytes | None, source: Path | None) -> None: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...

    @abstractmethod
    def write_manifest(self, manifest: dict[str, dict[str, Any]]) -> None: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    def close(self) -> None:
        """Release connections; targets without connections need nothing."""
        return None

    @abstractmethod
    def describe(self) -> str: ...


class FolderTarget(Target):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def describe(self) -> str:
        return f"folder {self.root}"

    def read_manifest(self) -> dict[str, dict[str, Any]] | None:
        p = self.root / MANIFEST_NAME
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return dict(data.get("files", {}))
        except (json.JSONDecodeError, AttributeError):
            return None

    def write(self, path: str, data: bytes | None, source: Path | None) -> None:
        dest = self._safe(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        if source is not None:
            shutil.copyfile(source, tmp)
        else:
            tmp.write_bytes(data or b"")
        os.replace(tmp, dest)

    def delete(self, path: str) -> None:
        dest = self._safe(path)
        if dest.is_file():
            dest.unlink()
            parent = dest.parent
            while parent != self.root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    def exists(self, path: str) -> bool:
        return self._safe(path).is_file()

    def write_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "files": manifest}, indent=1, sort_keys=True),
            encoding="utf-8",
        )

    def _safe(self, path: str) -> Path:
        dest = (self.root / path).resolve()
        if self.root.resolve() not in dest.parents and dest != self.root.resolve():
            raise ValueError(f"path escapes the target root: {path}")
        return dest


class SftpTarget(Target):
    """SFTP over SSH with the user's keys and agent; never a stored password."""

    def __init__(self, url: str) -> None:
        import paramiko

        parts = urlsplit(url)
        if parts.scheme != "sftp" or not parts.hostname:
            raise ValueError("sftp target must look like sftp://user@host[:port]/remote/path")
        self.remote_root = unquote(parts.path) or "/"
        self.host = parts.hostname
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            parts.hostname,
            port=parts.port or 22,
            username=parts.username,
            allow_agent=True,
            look_for_keys=True,
        )
        self.client = client
        self.sftp = client.open_sftp()

    def describe(self) -> str:
        return f"sftp {self.host}:{self.remote_root}"

    def close(self) -> None:
        self.sftp.close()
        self.client.close()

    def _remote(self, path: str) -> str:
        full = posixpath.normpath(posixpath.join(self.remote_root, path))
        if not full.startswith(posixpath.normpath(self.remote_root)):
            raise ValueError(f"path escapes the target root: {path}")
        return full

    def _mkdirs(self, directory: str) -> None:
        parts = PurePosixPath(directory).parts
        current = parts[0] if parts and parts[0] == "/" else ""
        for part in parts if current == "" else parts[1:]:
            current = posixpath.join(current, part) if current else part
            try:
                self.sftp.stat(current)
            except OSError:
                self.sftp.mkdir(current)

    def read_manifest(self) -> dict[str, dict[str, Any]] | None:
        try:
            with self.sftp.open(self._remote(MANIFEST_NAME), "r") as fh:
                data = json.loads(fh.read().decode("utf-8"))
            return dict(data.get("files", {}))
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def write(self, path: str, data: bytes | None, source: Path | None) -> None:
        remote = self._remote(path)
        self._mkdirs(posixpath.dirname(remote))
        tmp = remote + ".tmp"
        if source is not None:
            self.sftp.put(str(source), tmp)
        else:
            with self.sftp.open(tmp, "wb") as fh:
                fh.write(data or b"")
        with contextlib.suppress(OSError):
            self.sftp.remove(remote)
        self.sftp.rename(tmp, remote)

    def delete(self, path: str) -> None:
        with contextlib.suppress(OSError):
            self.sftp.remove(self._remote(path))

    def exists(self, path: str) -> bool:
        try:
            return stat.S_ISREG(self.sftp.stat(self._remote(path)).st_mode or 0)
        except OSError:
            return False

    def write_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        self._mkdirs(self.remote_root)
        with self.sftp.open(self._remote(MANIFEST_NAME), "wb") as fh:
            fh.write(
                json.dumps({"version": 1, "files": manifest}, indent=1, sort_keys=True).encode(
                    "utf-8"
                )
            )


def open_target(spec: str) -> Target:
    if spec.startswith("folder:"):
        return FolderTarget(Path(spec[len("folder:") :]).expanduser())
    if spec.startswith("sftp://"):
        return SftpTarget(spec)
    if spec.startswith("rsync:"):
        raise ValueError("rsync targets are driven by export_rsync(), not open_target()")
    if "://" not in spec and ":" not in spec[:2]:
        return FolderTarget(Path(spec).expanduser())
    raise ValueError(
        f"unknown target {spec!r}; use folder:/path, sftp://user@host/path or rsync:user@host:/path"
    )


def plan_export(files: ExportFiles, target: Target) -> Plan:
    old = target.read_manifest() or {}
    d = diff_manifests(old, files.manifest)
    # Files the manifest says exist but are gone on the target are re-sent.
    for p in list(files.manifest):
        if p in old and p not in d["changed"] and p not in d["added"] and not target.exists(p):
            d["added"].append(p)
    unchanged = len(files.manifest) - len(d["added"]) - len(d["changed"])
    return Plan(sorted(d["added"]), sorted(d["changed"]), sorted(d["removed"]), unchanged)


def apply_export(files: ExportFiles, target: Target, plan: Plan, *, delete: bool = True) -> None:
    for path in plan.added + plan.changed:
        data, source = files.files[path]
        target.write(path, data, source)
    if delete:
        for path in plan.removed:
            target.delete(path)
    manifest = dict(files.manifest)
    if not delete:
        old = target.read_manifest() or {}
        for path in plan.removed:
            manifest[path] = old[path]
    target.write_manifest(manifest)


def export_rsync(files: ExportFiles, spec: str, *, dry_run: bool, delete: bool = True) -> str:
    """Materialise the files in a temporary folder and hand them to rsync (needs the rsync binary)."""
    import tempfile

    rsync = shutil.which("rsync")
    if rsync is None:
        raise RuntimeError("rsync is not installed on this machine; use an sftp:// target instead")
    dest = spec[len("rsync:") :]
    with tempfile.TemporaryDirectory(prefix="constellation-export-") as tmp:
        folder = FolderTarget(Path(tmp))
        for path, (data, source) in files.files.items():
            folder.write(path, data, source)
        args = [rsync, "-rlt", "--chmod=F644,D755"]
        if delete:
            args.append("--delete")
        if dry_run:
            args.append("--dry-run")
        args += ["--itemize-changes", f"{tmp}/", dest]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"rsync exited with {proc.returncode}")
        return proc.stdout
