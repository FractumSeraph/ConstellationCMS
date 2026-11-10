"""Is there a newer ConstellationCMS? Asks GitHub's public "latest release" address once a day.

Nothing is downloaded or installed by itself: the studio shows a note with a download link,
picked for this computer (the Windows installer, the AppImage, the .deb, or the release page).
The check sends no information about this studio beyond an ordinary web request, and can be
turned off on the Account page.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
import time
from typing import Any

import httpx

from constellation import __version__

REPO = "FractumSeraph/ConstellationCMS"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DAY = 24 * 3600
KV_LATEST = "update:latest"
KV_CHECKED = "update:checked"
KV_DISMISSED = "update:dismissed"
KV_OFF = "update:off"


def parse_version(text: str) -> tuple[int, ...]:
    """``v1.2.3`` or ``1.2.3`` as (1, 2, 3); anything after the numbers (``-rc1``) is ignored."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return bool(a) and a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def install_kind() -> str:
    """How this copy was installed: ``windows``, ``appimage``, ``deb`` or ``python``."""
    if not getattr(sys, "frozen", False):
        return "python"
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    return "deb"


def pick_asset(assets: dict[str, str], kind: str | None = None) -> str:
    """The download for this computer, or "" when the release page is the best we can do."""
    kind = kind or install_kind()
    ending = {"windows": "-Setup.exe", "appimage": ".AppImage", "deb": ".deb"}.get(kind)
    if not ending:
        return ""
    for name, url in sorted(assets.items()):
        if name.endswith(ending):
            return url
    return ""


def enabled(state: Any) -> bool:
    return not state.kv_get(KV_OFF, False)


def check(
    state: Any, *, client: httpx.Client | None = None, force: bool = False, now: float | None = None
) -> dict[str, Any] | None:
    """Ask GitHub (at most once a day unless ``force``) and remember the answer."""
    now = time.time() if now is None else now
    last = state.kv_get(KV_CHECKED)
    if not force and (not enabled(state) or (last is not None and now - float(last) < DAY)):
        return state.kv_get(KV_LATEST)  # type: ignore[no-any-return]
    own = client is None
    c = client or httpx.Client(
        timeout=10, headers={"user-agent": f"ConstellationCMS/{__version__}"}
    )
    try:
        r = c.get(LATEST_API, headers={"accept": "application/vnd.github+json"})
        if r.status_code != 200:
            state.kv_set(KV_CHECKED, now)  # no release yet, or rate limited: try tomorrow
            return state.kv_get(KV_LATEST)  # type: ignore[no-any-return]
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return state.kv_get(KV_LATEST)  # type: ignore[no-any-return]  # offline: try again later
    finally:
        if own:
            c.close()
    latest = {
        "version": str(data.get("tag_name", "")).lstrip("v"),
        "page": str(data.get("html_url") or RELEASES_PAGE),
        "assets": {
            str(a.get("name")): str(a.get("browser_download_url"))
            for a in data.get("assets", [])
            if a.get("name") and a.get("browser_download_url")
        },
    }
    state.kv_set(KV_LATEST, latest)
    state.kv_set(KV_CHECKED, now)
    return latest


def available(state: Any, current: str = __version__) -> dict[str, str] | None:
    """What the studio should offer: {version, url, page}, or None when up to date."""
    latest = state.kv_get(KV_LATEST)
    if not latest or not enabled(state) or not newer(str(latest.get("version", "")), current):
        return None
    if state.kv_get(KV_DISMISSED) == latest["version"]:
        return None
    page = str(latest.get("page") or RELEASES_PAGE)
    return {
        "version": str(latest["version"]),
        "url": pick_asset(dict(latest.get("assets", {}))) or page,
        "page": page,
    }


def start_background(state: Any, interval: float = 6 * 3600) -> threading.Thread:
    """Check now (if a day has passed) and then every few hours, in a background thread."""

    def loop() -> None:
        while True:
            with contextlib.suppress(Exception):
                check(state)
            time.sleep(interval)

    t = threading.Thread(target=loop, name="update-check", daemon=True)
    t.start()
    return t
