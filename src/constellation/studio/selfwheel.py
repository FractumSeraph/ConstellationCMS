"""Package the ConstellationCMS the studio is running as a wheel, to install it on a server.

The server setup uploads this, so a new server runs exactly the version the studio does, with
no need for the server to reach PyPI or GitHub for ConstellationCMS itself (its dependencies
still come from PyPI). Works for a normal install, an installer build and a development checkout.
"""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from importlib import metadata
from pathlib import Path

DIST = "constellationcms"
WHEEL_FILE = "Wheel-Version: 1.0\nGenerator: constellation-selfwheel\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
ENTRY_POINTS = "[console_scripts]\nconstellation = constellation.cli:main\n"


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def build_wheel() -> tuple[str, bytes]:
    """(file name, wheel bytes) for the installed ConstellationCMS."""
    import constellation

    dist = metadata.distribution(DIST)
    version = dist.version
    package = Path(constellation.__file__).resolve().parent
    info = f"{DIST}-{version}.dist-info"
    files: list[tuple[str, bytes]] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        files.append(("constellation/" + path.relative_to(package).as_posix(), path.read_bytes()))
    meta_text = (
        dist.read_text("METADATA") or f"Metadata-Version: 2.1\nName: {DIST}\nVersion: {version}\n"
    )
    files.append((f"{info}/METADATA", meta_text.encode("utf-8")))
    files.append((f"{info}/WHEEL", WHEEL_FILE.encode()))
    files.append(
        (f"{info}/entry_points.txt", (dist.read_text("entry_points.txt") or ENTRY_POINTS).encode())
    )
    record = [_record_line(name, data) for name, data in files] + [f"{info}/RECORD,,"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
        zf.writestr(f"{info}/RECORD", "\n".join(record) + "\n")
    return f"{DIST}-{version}-py3-none-any.whl", buf.getvalue()
