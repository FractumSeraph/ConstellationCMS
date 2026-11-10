"""Static export of one platform's build to a folder, over SFTP, with rsync, or to a git
repository such as a GitHub Pages site.

Every target keeps a small manifest (``.constellation-export.json``) at its root so the next
export can compute a diff (added, changed, removed) without hashing remote files, and so that
deletion only ever touches files this tool wrote. Git targets are the exception: the
repository itself is the record, so they hash the files git tracks instead (see
:class:`GitTarget`).
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import posixpath
import re
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


def layout_files(
    build: Build, platform: str, layout: str, site: Site, *, pages: bool = False
) -> ExportFiles:
    """Arrange a platform build for a target layout.

    ``plain`` is the release tree. ``agate`` nests the Gemini tree under ``content/<hostname>/``
    and adds a ``.meta`` sidecar so Agate serves feeds with the right media type.

    Web exports also get a small forwarding page for each of the site's redirects, because
    ordinary static hosts cannot send redirects themselves. ``pages`` adds what GitHub Pages
    needs: ``CNAME`` with the site's web hostname and an empty ``.nojekyll``.
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
            _add(out, prefix + ".meta", data, "text/plain")
    if platform == "web":
        for path, data in redirect_pages(site).items():
            if path not in out.files:
                _add(out, path, data, "text/html")
    if pages:
        if platform != "web":
            raise ValueError("GitHub Pages serves the web platform only")
        host = pages_hostname(site)
        if host:
            _add(out, "CNAME", (host + "\n").encode(), "text/plain")
        _add(out, ".nojekyll", b"", "text/plain")
    return out


def _add(out: ExportFiles, path: str, data: bytes, media_type: str) -> None:
    out.files[path] = (data, None)
    out.manifest[path] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "type": media_type,
    }


def pages_hostname(site: Site) -> str:
    """The custom domain for a GitHub Pages ``CNAME`` file, or "" for a ``*.github.io`` site."""
    web = site.config.platform("web")
    host = web.hostnames[0] if web.hostnames else urlsplit(web.base_url).hostname or ""
    if not host or host == "localhost" or host.endswith(".github.io"):
        return ""
    return host


def redirect_pages(site: Site) -> dict[str, bytes]:
    """A forwarding page for each redirect whose source is a path on this site.

    ``/old/`` becomes ``old/index.html`` and ``/old.html`` stays ``old.html``. The page uses a
    meta refresh (which works without JavaScript), a canonical link so search engines move to the
    new address, and a script that keeps any ``#fragment``.
    """
    pages: dict[str, bytes] = {}
    for r in site.config.redirects:
        src = r.from_path
        if not src.startswith("/") or src.startswith("//") or "*" in src or ".." in src:
            continue
        rel = src.lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        elif not rel.endswith((".html", ".htm")):
            rel += "/index.html"
        to = html.escape(r.to, quote=True)
        script = json.dumps(r.to).replace("<", "\\u003c")
        pages[rel] = (
            "<!doctype html>\n"
            '<html><head><meta charset="utf-8">\n'
            "<title>Moved</title>\n"
            f'<link rel="canonical" href="{to}">\n'
            f'<meta http-equiv="refresh" content="0; url={to}">\n'
            '<meta name="robots" content="noindex">\n'
            f"<script>location.replace({script} + location.hash)</script>\n"
            f'</head><body><p>This page has moved to <a href="{to}">{to}</a>.</p></body></html>\n'
        ).encode()
    return pages


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

    def finish(self, summary: str) -> None:
        """Called once after every write and delete; git targets commit and push here."""
        return None

    def keeps(self, path: str) -> bool:
        """True for a file the target never deletes even when the site no longer has it."""
        return False

    def local_manifest(self, files: ExportFiles) -> dict[str, dict[str, Any]]:
        """The files to export, described the way :meth:`read_manifest` describes the target's."""
        return files.manifest

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


GITHUB_SPEC = re.compile(
    r"^github:(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9._-]{1,100})"
    r"(?:#(?P<branch>[A-Za-z0-9._/-]{1,200}))?$"
)
# Files a Pages repository keeps even though the site does not produce them.
GIT_KEEP = re.compile(
    r"^(\.github/|\.gitignore$|\.gitattributes$|README[^/]*$|LICEN[CS]E[^/]*$|CNAME$)"
)


def remove_tree(path: Path) -> None:
    """Delete a folder, including the read-only files git leaves on Windows."""

    def retry(func: Any, name: str, exc: BaseException) -> None:
        os.chmod(name, stat.S_IWRITE)
        func(name)

    shutil.rmtree(path, onexc=retry)


def is_git_target(spec: str) -> bool:
    return spec.startswith(("github:", "git:"))


def parse_git_spec(spec: str) -> tuple[str, str, str]:
    """``github:owner/repo[#branch]`` or ``git:URL-or-path[#branch]`` → (url, branch, label)."""
    if spec.startswith("github:"):
        m = GITHUB_SPEC.match(spec)
        if not m or m.group("repo") in (".", ".."):
            raise ValueError(
                "a GitHub target looks like github:owner/repository or github:owner/repository#branch"
            )
        owner, repo = m.group("owner"), m.group("repo").removesuffix(".git")
        return f"https://github.com/{owner}/{repo}.git", m.group("branch") or "", f"{owner}/{repo}"
    if spec.startswith("git:"):
        rest = spec[len("git:") :]
        url, _, branch = rest.partition("#")
        if not url or url.startswith("-") or any(c in rest for c in " \t\n\"'`;|&$<>"):
            raise ValueError("a git target looks like git:https://host/owner/repo.git#branch")
        if branch and not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", branch):
            raise ValueError(f"bad branch name {branch!r}")
        return url, branch, url
    raise ValueError(f"not a git target: {spec!r}")


class GitTarget(Target):
    """A git repository, such as the one behind a GitHub Pages site.

    The repository is cloned once into the cache folder and brought up to date with the remote
    before every export, so it always starts from what is published. Writes go into that
    working copy; :meth:`finish` commits them and pushes. Pushing uses git's own sign-in (for
    GitHub, ``gh auth setup-git`` or Git Credential Manager), never a stored password.

    The files git tracks are the record of what is published, so no manifest file is kept. The
    first export into a repository made by another tool replaces its files (old versions stay in
    the history); a README, licence, ``.gitignore``, ``CNAME`` and ``.github/`` are kept.
    """

    def __init__(self, spec: str, *, cache_dir: Path | None = None) -> None:
        self.url, branch, self.label = parse_git_spec(spec)
        if shutil.which("git") is None:
            raise RuntimeError(
                "git is not installed on this machine. For GitHub, sign in with GitHub on the "
                "studio's Account page instead (no git needed), or install git"
                if spec.startswith("github:")
                else "git is not installed on this machine; install it to publish to a git repository"
            )
        if cache_dir is None:
            from constellation.studio.state import default_cache_dir

            cache_dir = default_cache_dir() / "git"
        name = (
            re.sub(r"[^A-Za-z0-9._-]+", "-", self.label.rstrip("/").split("/")[-1])[-40:] or "repo"
        )
        self.workdir = (
            Path(cache_dir) / f"{name}-{hashlib.sha256(self.url.encode()).hexdigest()[:10]}"
        )
        self.branch = self._sync(branch)
        self.folder = FolderTarget(self.workdir)

    def describe(self) -> str:
        return f"git {self.label} ({self.branch})"

    def _git(
        self, *args: str, check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        proc = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", *args],
            cwd=cwd or self.workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip()
            if "Authentication failed" in message or "could not read Username" in message:
                message += (
                    "\ngit could not sign in. For GitHub, run `gh auth login` and then `gh auth setup-git`, "
                    "or sign in once with Git Credential Manager."
                )
            raise RuntimeError(f"git {args[0]} failed: {message}")
        return proc

    def _sync(self, branch: str) -> str:
        """Clone or refresh the working copy and return the branch it is on."""
        if not (self.workdir / ".git").is_dir():
            if self.workdir.exists():
                remove_tree(self.workdir)
            self.workdir.parent.mkdir(parents=True, exist_ok=True)
            args = ["clone", "--quiet", "--no-tags", "--single-branch"]
            if branch:
                args += ["--branch", branch]
            proc = self._git(
                *args, "--", self.url, str(self.workdir), check=False, cwd=self.workdir.parent
            )
            if proc.returncode != 0 and branch and "not found in upstream" in proc.stderr:
                # A branch that does not exist yet starts empty.
                if self.workdir.exists():
                    remove_tree(self.workdir)
                self._git(
                    "clone",
                    "--quiet",
                    "--no-tags",
                    "--",
                    self.url,
                    str(self.workdir),
                    cwd=self.workdir.parent,
                )
                self._git("checkout", "--quiet", "--orphan", branch)
                self._git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".")
                self._git("clean", "-fdxq")
                return branch
            if proc.returncode != 0:
                raise RuntimeError(f"git clone failed: {(proc.stderr or proc.stdout).strip()}")
            self._git("config", "core.autocrlf", "false")
        else:
            self._git("remote", "set-url", "origin", self.url)
        has_commits = self._git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
        if not branch:
            head = self._git("symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
            branch = head or "main"
        remote_has = (
            self._git(
                "ls-remote", "--exit-code", "--heads", "origin", branch, check=False
            ).returncode
            == 0
        )
        if remote_has:
            self._git(
                "fetch",
                "--quiet",
                "--no-tags",
                "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            )
            self._git("checkout", "--quiet", "-B", branch, f"origin/{branch}")
            self._git("reset", "--quiet", "--hard", f"origin/{branch}")
        elif (
            has_commits
            and self._git("symbolic-ref", "--short", "HEAD", check=False).stdout.strip() != branch
        ):
            self._git("checkout", "--quiet", "--orphan", branch)
            self._git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".")
        elif not has_commits:
            self._git("symbolic-ref", "HEAD", f"refs/heads/{branch}")
        self._git("clean", "-fdxq")
        return branch

    def _check(self, path: str) -> None:
        first = PurePosixPath(path).parts[:1]
        if first and first[0].lower() == ".git":
            raise ValueError(f"refusing to write inside .git: {path}")

    def keeps(self, path: str) -> bool:
        return bool(GIT_KEEP.match(path))

    def read_manifest(self) -> dict[str, dict[str, Any]] | None:
        out = self._git("ls-files", "-z").stdout
        manifest: dict[str, dict[str, Any]] = {}
        for path in filter(None, out.split("\0")):
            f = self.workdir / path
            if f.is_file():
                data = f.read_bytes()
                manifest[path] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        return manifest

    def write(self, path: str, data: bytes | None, source: Path | None) -> None:
        self._check(path)
        self.folder.write(path, data, source)

    def delete(self, path: str) -> None:
        self._check(path)
        self.folder.delete(path)

    def exists(self, path: str) -> bool:
        return self.folder.exists(path)

    def write_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        return None  # the repository is the record

    def finish(self, summary: str) -> None:
        self._git("add", "-A")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        identity: list[str] = []
        if not self._git("config", "user.email", check=False).stdout.strip():
            identity = [
                "-c",
                "user.name=ConstellationCMS",
                "-c",
                "user.email=constellation@localhost",
            ]
        self._git(
            *identity, "commit", "--quiet", "-m", f"Publish with ConstellationCMS\n\n{summary}"
        )
        self._git("push", "--quiet", "origin", f"HEAD:refs/heads/{self.branch}")


def open_target(spec: str) -> Target:
    if is_git_target(spec):
        return GitTarget(spec)
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
    new = target.local_manifest(files)
    d = diff_manifests(old, new)
    # Files the manifest says exist but are gone on the target are re-sent.
    for p in list(new):
        if p in old and p not in d["changed"] and p not in d["added"] and not target.exists(p):
            d["added"].append(p)
    unchanged = len(new) - len(d["added"]) - len(d["changed"])
    removed = [p for p in d["removed"] if not target.keeps(p)]
    return Plan(sorted(d["added"]), sorted(d["changed"]), sorted(removed), unchanged)


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
    target.finish(plan.summary())


def export_rsync(files: ExportFiles, spec: str, *, dry_run: bool, delete: bool = False) -> str:
    """Materialise the files in a temporary folder and hand them to rsync (needs the rsync binary).

    rsync cannot tell which remote files an earlier export wrote, so ``delete`` (rsync's
    ``--delete``) removes *everything* in the destination that is not in this export. It is
    off unless asked for explicitly.
    """
    import tempfile

    rsync = shutil.which("rsync")
    if rsync is None:
        raise RuntimeError("rsync is not installed on this machine; use an sftp:// target instead")
    dest = spec[len("rsync:") :]
    if not dest or dest.startswith("-") or any(c in dest for c in " \t\n'\"`;|&$"):
        raise ValueError(f"bad rsync destination {dest!r}; use rsync:user@host:/path")
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
