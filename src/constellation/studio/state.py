"""Machine-local state: settings, secrets, sessions and the change-detection index.

Nothing here is ever written into the synced workspace.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Run a database method under the state's lock.

    The studio serves requests from several threads but shares one SQLite connection; without
    the lock, two requests at once can fail with "bad parameter or other API misuse".
    """

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0]._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


def default_state_dir() -> Path:
    env = os.environ.get("CONSTELLATION_STATE_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ConstellationCMS"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "constellation"


def default_cache_dir() -> Path:
    env = os.environ.get("CONSTELLATION_CACHE_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ConstellationCMS" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "constellation"


@dataclass
class StudioSettings:
    workspace: str = ""
    password_hash: str = ""
    auto_login_local: bool = True
    remote_mode: bool = False
    trust_proxy: bool = False
    bind: str = "127.0.0.1"
    port: int = 8787
    secret_key: str = ""
    machine_id: str = ""
    totp_secret: str = ""
    look: str = ""  # empty until chosen; drawn as the default look
    mode: str = "night"
    extra: dict[str, Any] = field(default_factory=dict)

    def effective_auto_login(self) -> bool:
        """Auto-login is only ever allowed when the request genuinely comes from this machine."""
        return self.auto_login_local and not self.remote_mode and not self.trust_proxy


class State:
    def __init__(self, state_dir: Path | None = None, cache_dir: Path | None = None) -> None:
        self.dir = Path(state_dir or default_state_dir())
        self.cache_dir = Path(cache_dir or default_cache_dir())
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.dir / "studio.toml"
        self.settings = self._load_settings()
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.dir / "studio.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # ---- settings ---------------------------------------------------------------------------
    def _load_settings(self) -> StudioSettings:
        s = StudioSettings()
        if self.settings_path.is_file():
            data = tomlkit.parse(self.settings_path.read_text(encoding="utf-8"))
            for key in list(StudioSettings.__dataclass_fields__):
                if key in data and key != "extra":
                    setattr(s, key, data[key])
        changed = False
        if not s.secret_key:
            s.secret_key = secrets.token_urlsafe(32)
            changed = True
        if not s.machine_id:
            s.machine_id = uuid.uuid4().hex[:12]
            changed = True
        if changed:
            self._save_settings(s)
        return s

    def _save_settings(self, s: StudioSettings) -> None:
        doc = tomlkit.document()
        for key in StudioSettings.__dataclass_fields__:
            if key == "extra":
                continue
            doc[key] = getattr(s, key)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
        os.replace(tmp, self.settings_path)
        with contextlib.suppress(OSError):
            os.chmod(self.settings_path, 0o600)

    def save_settings(self) -> None:
        self._save_settings(self.settings)

    # ---- database ---------------------------------------------------------------------------
    @_locked
    def _migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL,
                user_agent TEXT, remote TEXT, csrf TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_index (
                site TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, mtime REAL NOT NULL,
                PRIMARY KEY (site, path)
            );
            CREATE TABLE IF NOT EXISTS audit (
                ts REAL NOT NULL, event TEXT NOT NULL, detail TEXT, remote TEXT
            );
            CREATE TABLE IF NOT EXISTS login_failures (
                key TEXT PRIMARY KEY, count INTEGER NOT NULL, locked_until REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        self.db.commit()

    # ---- sessions ---------------------------------------------------------------------------
    SESSION_IDLE = 12 * 3600
    SESSION_MAX = 30 * 24 * 3600

    @_locked
    def create_session(self, user_agent: str, remote: str) -> tuple[str, str]:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
            (sid, now, now, user_agent[:200], remote, csrf),
        )
        self.db.commit()
        return sid, csrf

    @_locked
    def touch_session(self, sid: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT id, created, last_seen, csrf FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        if row is None:
            return None
        now = time.time()
        if now - row[2] > self.SESSION_IDLE or now - row[1] > self.SESSION_MAX:
            self.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self.db.commit()
            return None
        self.db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, sid))
        self.db.commit()
        return {"id": row[0], "created": row[1], "csrf": row[3]}

    @_locked
    def delete_session(self, sid: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.db.commit()

    @_locked
    def delete_all_sessions(self) -> None:
        self.db.execute("DELETE FROM sessions")
        self.db.commit()

    # ---- login rate limiting ----------------------------------------------------------------
    LOCK_AFTER = 5
    LOCK_SECONDS = 15 * 60

    @_locked
    def login_locked(self, key: str) -> float:
        row = self.db.execute(
            "SELECT locked_until FROM login_failures WHERE key=?", (key,)
        ).fetchone()
        if row and row[0] > time.time():
            return float(row[0] - time.time())
        return 0.0

    @_locked
    def login_failed(self, key: str) -> None:
        row = self.db.execute("SELECT count FROM login_failures WHERE key=?", (key,)).fetchone()
        count = (row[0] if row else 0) + 1
        until = time.time() + self.LOCK_SECONDS if count >= self.LOCK_AFTER else 0
        self.db.execute(
            "INSERT OR REPLACE INTO login_failures VALUES (?,?,?)",
            (key, count if until == 0 else 0, until),
        )
        self.db.commit()

    @_locked
    def login_succeeded(self, key: str) -> None:
        self.db.execute("DELETE FROM login_failures WHERE key=?", (key,))
        self.db.commit()

    # ---- audit ------------------------------------------------------------------------------
    @_locked
    def audit(self, event: str, detail: str = "", remote: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit VALUES (?,?,?,?)", (time.time(), event, detail[:500], remote)
        )
        self.db.commit()

    @_locked
    def audit_tail(self, n: int = 50) -> list[tuple[float, str, str, str]]:
        return list(
            self.db.execute(
                "SELECT ts, event, detail, remote FROM audit ORDER BY ts DESC LIMIT ?", (n,)
            )
        )

    # ---- file index (change detection) ------------------------------------------------------
    @_locked
    def index_get(self, site: str, path: str) -> tuple[str, float] | None:
        row = self.db.execute(
            "SELECT sha256, mtime FROM file_index WHERE site=? AND path=?", (site, path)
        ).fetchone()
        return (row[0], row[1]) if row else None

    @_locked
    def index_set(self, site: str, path: str, sha256: str, mtime: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO file_index VALUES (?,?,?,?)", (site, path, sha256, mtime)
        )
        self.db.commit()

    @_locked
    def index_delete(self, site: str, path: str) -> None:
        self.db.execute("DELETE FROM file_index WHERE site=? AND path=?", (site, path))
        self.db.commit()

    # ---- kv ---------------------------------------------------------------------------------
    @_locked
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @_locked
    def kv_set(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    @_locked
    def close(self) -> None:
        self.db.close()
