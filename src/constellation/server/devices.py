"""The computers (studios) allowed to manage this server, and pairing codes to add more.

Each studio gets its own credential, ``cst_admin_d<id>_<secret>``, so one can be removed without
touching the others. The credential is random and long, so it is stored as a SHA-256 hash (no
slow hashing needed) in ``devices.json`` in the server's data folder, readable only by the
service.

A pairing code is how a new computer gets its credential without anyone copying a token: a
studio that is already connected asks for a code (``ABCD-EFGH``), the new computer enters it
with the server's address, and the server swaps it for a credential. Codes last ten minutes,
work once, live only in memory, and too many wrong codes pause pairing for everyone for a while.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PREFIX = "cst_admin_d"
KEY = re.compile(r"^cst_admin_d([a-z0-9]{8})_([A-Za-z0-9_-]{30,})$")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_SECONDS = 600
MAX_BAD_CODES = 10  # per window, for the whole server
BAD_CODE_WINDOW = 600


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Device:
    id: str
    label: str
    hash: str
    created: float
    last_used: float = 0.0

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "created": self.created,
            "last_used": self.last_used,
        }


class Devices:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "devices.json"
        self._lock = threading.Lock()

    def _load(self) -> list[Device]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        out = []
        for d in data.get("devices", []) if isinstance(data, dict) else []:
            with contextlib.suppress(TypeError, KeyError):
                out.append(
                    Device(
                        **{
                            k: d[k]
                            for k in ("id", "label", "hash", "created", "last_used")
                            if k in d
                        }
                    )
                )
        return out

    def _save(self, devices: list[Device]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "devices": [asdict(d) for d in devices]}, fh, indent=1)
        os.replace(tmp, self.path)

    def list(self) -> list[Device]:
        with self._lock:
            return self._load()

    def add(self, label: str) -> tuple[Device, str]:
        """A new credential; the raw value is returned once and never stored."""
        label = re.sub(r"\s+", " ", label).strip()[:80] or "studio"
        with self._lock:
            devices = self._load()
            ids = {d.id for d in devices}
            dev_id = secrets.token_hex(4)
            while dev_id in ids:
                dev_id = secrets.token_hex(4)
            raw = f"{PREFIX}{dev_id}_{secrets.token_urlsafe(32)}"
            device = Device(dev_id, label, _sha(raw), time.time())
            devices.append(device)
            self._save(devices)
        return device, raw

    def verify(self, raw: str) -> str | None:
        """The id of the device this credential belongs to, or None."""
        m = KEY.match(raw or "")
        if not m:
            return None
        digest = _sha(raw)
        with self._lock:
            devices = self._load()
            for d in devices:
                if d.id == m.group(1) and hmac.compare_digest(d.hash, digest):
                    now = time.time()
                    if now - d.last_used > 3600:  # write at most hourly
                        d.last_used = now
                        self._save(devices)
                    return d.id
        return None

    def remove(self, dev_id: str) -> bool:
        with self._lock:
            devices = self._load()
            keep = [d for d in devices if d.id != dev_id]
            if len(keep) == len(devices):
                return False
            self._save(keep)
            return True


class Pairings:
    def __init__(self) -> None:
        self._codes: dict[str, float] = {}
        self._bad: list[float] = []
        self._lock = threading.Lock()

    @staticmethod
    def normalise(code: str) -> str:
        return "".join(c for c in code.upper() if c.isalnum())

    def new(self) -> str:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
        with self._lock:
            now = time.monotonic()
            self._codes = {k: t for k, t in self._codes.items() if t > now}
            self._codes[_sha(raw)] = now + PAIRING_SECONDS
        return f"{raw[:4]}-{raw[4:]}"

    def locked(self) -> bool:
        now = time.monotonic()
        with self._lock:
            self._bad = [t for t in self._bad if now - t < BAD_CODE_WINDOW]
            return len(self._bad) >= MAX_BAD_CODES

    def redeem(self, code: str) -> bool:
        """True (and the code is used up) when ``code`` is a live pairing code."""
        if self.locked():
            return False
        key = _sha(self.normalise(code))
        now = time.monotonic()
        with self._lock:
            expires = self._codes.pop(key, None)
            if expires is not None and expires > now:
                return True
            self._bad.append(now)
            return False
