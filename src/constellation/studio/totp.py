"""Time-based one-time passwords (RFC 6238) with no third-party dependency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def totp(secret: str, *, at: float | None = None, step: int = 30) -> str:
    return _code(secret, int((at if at is not None else time.time()) // step))


def match(
    secret: str, code: str, *, at: float | None = None, step: int = 30, window: int = 1
) -> int | None:
    """The time step a code belongs to (current, or ``window`` steps either side), or None."""
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return None
    now = int((at if at is not None else time.time()) // step)
    found: int | None = None
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_code(secret, now + offset), code):
            found = now + offset
    return found


def verify(
    secret: str,
    code: str,
    *,
    at: float | None = None,
    step: int = 30,
    window: int = 1,
    after: int | None = None,
) -> bool:
    """Accept the current code and ``window`` steps either side, in constant time.

    With ``after``, a code from that time step or earlier is refused, so a code that was
    already used to sign in cannot be replayed.
    """
    counter = match(secret, code, at=at, step=step, window=window)
    return counter is not None and (after is None or counter > after)


def provisioning_uri(secret: str, account: str, issuer: str = "ConstellationCMS") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def qr_svg(data: str) -> str:
    """An inline SVG QR code (no raster, no external request)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    return str(img.to_string(encoding="unicode"))
