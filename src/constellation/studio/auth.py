"""Passwords, sessions and request authentication for the studio."""

from __future__ import annotations

import ipaddress

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, ValueError):
        return False


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def host_is_loopback(host_header: str | None) -> bool:
    """True when a Host header names this machine (``localhost``, ``127.0.0.1``, ``[::1]``).

    Loopback auto-login also requires this, so a DNS-rebinding page that reaches the studio
    through a hostile name never gets a session.
    """
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    return is_loopback(host.rstrip("."))


def safe_next(target: str | None) -> str:
    """A post-login redirect limited to paths on this studio."""
    target = (target or "/").strip()
    if not target.startswith("/") or target.startswith(("//", "/\\")) or "\\" in target:
        return "/"
    return target
