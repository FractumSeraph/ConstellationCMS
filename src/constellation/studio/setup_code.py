"""The one-time code that lets a studio on a server be set up from anywhere.

A studio on your own computer is set up at http://localhost:8787 with just a password. A studio
on a server is reached over the internet, so whoever arrives first must not be able to claim it:
first-run setup from another computer needs this code. The server installer creates it and
prints the setup link (``https://studio.example.org/setup?code=…``), the studio prints it when it
starts, and a desktop studio connected to the server shows it on its Servers page. Setting up
the studio deletes the code.
"""

from __future__ import annotations

import contextlib
import hmac
import os
import secrets
from pathlib import Path

FILE_NAME = "setup-code"
# No 0/O or 1/I/L, so a code read off a screen is typed right.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def path(state_dir: Path) -> Path:
    return Path(state_dir) / FILE_NAME


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def read(state_dir: Path) -> str:
    try:
        return path(state_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure(state_dir: Path) -> str:
    """The current code, creating one (readable only by its owner) if there is none."""
    existing = read(state_dir)
    if existing:
        return existing
    code = new_code()
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(code + "\n")
    return code


def normalise(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def matches(state_dir: Path, given: str) -> bool:
    expected = read(state_dir)
    if not expected or not given:
        return False
    return hmac.compare_digest(normalise(expected), normalise(given))


def clear(state_dir: Path) -> None:
    with contextlib.suppress(OSError):
        path(state_dir).unlink()


def setup_link(public_url: str, code: str) -> str:
    return f"{public_url.rstrip('/')}/setup?code={code}"
