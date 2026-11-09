"""Per-machine lock files inside a site's ``.constellation/locks/`` folder.

Each studio instance writes only its own file, so sync clients never see two writers of one
file. Another machine's fresh lock makes this instance open the site read-only until taken over.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from constellation.studio.workspace import atomic_write

STALE_AFTER = 5 * 60


@dataclass
class Lock:
    machine_id: str
    label: str
    heartbeat: float

    @property
    def fresh(self) -> bool:
        return time.time() - self.heartbeat < STALE_AFTER


def lock_dir(site_root: Path) -> Path:
    return site_root / ".constellation" / "locks"


def write_lock(site_root: Path, machine_id: str, label: str) -> None:
    atomic_write(
        lock_dir(site_root) / f"{machine_id}.json",
        json.dumps({"machine_id": machine_id, "label": label, "heartbeat": time.time()}),
    )


def release_lock(site_root: Path, machine_id: str) -> None:
    p = lock_dir(site_root) / f"{machine_id}.json"
    with contextlib.suppress(OSError):
        p.unlink()


def other_locks(site_root: Path, machine_id: str) -> list[Lock]:
    out: list[Lock] = []
    d = lock_dir(site_root)
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        if f.stem == machine_id:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(
                Lock(
                    str(data.get("machine_id", f.stem)),
                    str(data.get("label", "")),
                    float(data.get("heartbeat", 0)),
                )
            )
        except (OSError, ValueError):
            continue
    return out


def held_elsewhere(site_root: Path, machine_id: str) -> Lock | None:
    for lock in other_locks(site_root, machine_id):
        if lock.fresh:
            return lock
    return None


def take_over(site_root: Path, machine_id: str) -> None:
    """Remove stale locks from other machines (only allowed once they are stale)."""
    for lock in other_locks(site_root, machine_id):
        if not lock.fresh:
            release_lock(site_root, lock.machine_id)


def machine_label() -> str:
    try:
        import socket

        return socket.gethostname()
    except OSError:
        return os.environ.get("COMPUTERNAME", "unknown")
