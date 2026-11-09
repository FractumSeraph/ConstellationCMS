"""Self-checks for support questions: the Doctor page and ``constellation doctor``."""

from __future__ import annotations

import os
import platform
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from constellation import __version__


@dataclass(slots=True)
class Check:
    name: str
    status: str  # ok | warn | fail | info
    detail: str


def _sync_client(path: Path) -> str:
    markers = {
        ".dropbox": "Dropbox",
        ".dropbox.cache": "Dropbox",
        ".stfolder": "Syncthing",
        ".stversions": "Syncthing",
    }
    for p in [path, *path.parents]:
        for marker, name in markers.items():
            if (p / marker).exists():
                return name
        if "onedrive" in p.name.lower():
            return "OneDrive"
    return ""


def _port_free(bind: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((bind if bind not in ("0.0.0.0", "::") else "127.0.0.1", port))
            return True
        except OSError:
            return False


def run_checks(
    state: Any, workspace_root: Path, *, servers: list[Any] | None = None, running: bool = False
) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "Version",
            "info",
            f"ConstellationCMS {__version__} on Python {platform.python_version()} ({sys.platform})",
        )
    )
    ws = Path(workspace_root)
    if ws.is_dir() and os.access(ws, os.W_OK):
        checks.append(Check("Workspace", "ok", f"{ws} is writable"))
    else:
        checks.append(Check("Workspace", "fail", f"{ws} is missing or not writable"))
    client = _sync_client(ws)
    checks.append(
        Check(
            "Sync client",
            "info" if client else "warn",
            f"{client} detected; conflict files are reported on each site's dashboard"
            if client
            else "no Dropbox, OneDrive or Syncthing marker found; the workspace may not be synced",
        )
    )
    state_dir = Path(state.dir)
    checks.append(
        Check(
            "Machine-local state", "ok" if os.access(state_dir, os.W_OK) else "fail", str(state_dir)
        )
    )
    try:
        mode = oct(state.settings_path.stat().st_mode & 0o777)
        if sys.platform != "win32" and mode not in ("0o600", "0o400"):
            checks.append(
                Check(
                    "Secrets file permissions",
                    "warn",
                    f"{state.settings_path} is {mode}; expected 0o600",
                )
            )
        else:
            checks.append(
                Check("Secrets file permissions", "ok", f"{state.settings_path} ({mode})")
            )
    except OSError:
        checks.append(Check("Secrets file permissions", "warn", "studio.toml not found yet"))
    s = state.settings
    if s.remote_mode:
        checks.append(
            Check(
                "Remote mode",
                "ok" if s.totp_secret else "fail",
                "on, with second factor"
                if s.totp_secret
                else "on WITHOUT a second factor; this should not be possible",
            )
        )
    else:
        checks.append(Check("Remote mode", "info", "off (local only)"))
    if not running:
        free = _port_free(s.bind or "127.0.0.1", s.port or 8787)
        checks.append(
            Check(
                "Studio port",
                "ok" if free else "fail",
                f"{s.bind}:{s.port} {'is free' if free else 'is in use (is another studio running?)'}",
            )
        )
    try:
        import PIL  # noqa: F401

        checks.append(
            Check("Image support", "ok", "Pillow available; responsive variants will be generated")
        )
    except ImportError:  # pragma: no cover
        checks.append(Check("Image support", "warn", "Pillow missing"))
    for server in servers or []:
        checks.append(_server_check(server))
    return checks


def _server_check(server: Any) -> Check:
    import httpx

    url = server.admin_url.rstrip("/") + "/healthz"
    try:
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            return Check(f"Server {server.id}", "ok", f"{url}: {r.json().get('version', 'ok')}")
        return Check(f"Server {server.id}", "warn", f"{url}: HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return Check(f"Server {server.id}", "fail", f"{url}: {e.__class__.__name__}: {e}")


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks) if checks else 10
    return "\n".join(f"{c.status.upper():5} {c.name.ljust(width)}  {c.detail}" for c in checks)
