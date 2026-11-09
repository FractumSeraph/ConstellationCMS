"""The studio's looks (visual styles), day/night mode, and the small constellation drawings.

A look only changes how the studio is drawn; it never changes what any page does. The CSS for
every look lives in ``static/studio.css`` and is selected by ``data-look`` and ``data-mode``
attributes on ``<html>``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from markupsafe import Markup


@dataclass(frozen=True)
class Look:
    id: str
    name: str
    blurb: str


LOOKS: tuple[Look, ...] = (
    Look(
        "starchart",
        "Star chart",
        "Pure black, thin silver lines and plenty of room. Lists become stars joined by dotted lines.",
    ),
    Look(
        "observatory",
        "Observatory",
        "A faint, still starfield with soft glows and see-through panels. The most striking.",
    ),
    Look(
        "terminal",
        "Night terminal",
        "All monospace, dotted rules and Gemini-style markers, like the fractum site theme.",
    ),
)
DEFAULT_LOOK = "starchart"
LOOK_IDS = frozenset(look.id for look in LOOKS)

MODES: tuple[tuple[str, str], ...] = (
    ("night", "Night"),
    ("day", "Day"),
    ("system", "Follow this computer"),
)
DEFAULT_MODE = "night"
MODE_IDS = frozenset(m for m, _ in MODES)


def effective_look(value: str) -> str:
    """The look to draw: the saved one, or the default when none was chosen or it is unknown."""
    return value if value in LOOK_IDS else DEFAULT_LOOK


def effective_mode(value: str) -> str:
    return value if value in MODE_IDS else DEFAULT_MODE


def _points(
    seed: str, count: int, width: float, height: float, pad: float
) -> list[tuple[float, float, float]]:
    """Deterministic star positions and sizes from a seed string, spread left to right."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    pts: list[tuple[float, float, float]] = []
    span = (width - 2 * pad) / count
    for i in range(count):
        a, b, c = digest[(3 * i) % 32], digest[(3 * i + 1) % 32], digest[(3 * i + 2) % 32]
        x = pad + span * i + span * (a / 255) * 0.8
        y = pad + (height - 2 * pad) * (b / 255)
        r = 0.9 + 1.3 * (c / 255)
        pts.append((round(x, 1), round(y, 1), round(r, 2)))
    return pts


def site_stars(site_id: str) -> Markup:
    """A small constellation drawn from the site id, so each site card is easy to tell apart."""
    digest = hashlib.sha256(site_id.encode("utf-8")).digest()
    count = 4 + digest[31] % 4  # 4 to 7 stars
    pts = _points(site_id, count, 96, 40, 5)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (x, y, _) in enumerate(pts))
    stars = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in pts)
    return Markup(
        '<svg class="site-stars" viewBox="0 0 96 40" width="96" height="40" aria-hidden="true">'
        f'<path d="{path}"/><g>{stars}</g></svg>'
    )


_UPLOADED = re.compile(r"^uploaded (\d+)/(\d+)$")
_TO_UPLOAD = re.compile(r"^release \S+: (\d+) blobs to upload$")
_ACTIVE = re.compile(r"^release \S+ is active$")


def job_progress(job: Any) -> dict[str, Any] | None:
    """How far a server publish has got, read from its log, or None when it is not uploading."""
    if getattr(job, "kind", "") != "publish":
        return None
    total: int | None = None
    done = 0
    active = False
    for line in getattr(job, "log", []):
        if m := _TO_UPLOAD.match(line):
            total, done = int(m.group(1)), 0
        elif m := _UPLOADED.match(line):
            done, total = int(m.group(1)), int(m.group(2))
        elif _ACTIVE.match(line):
            active = True
    if total is None:
        return None
    if active:
        done = total
    fraction = 1.0 if total == 0 else min(1.0, done / total)
    return {"done": done, "total": total, "fraction": round(fraction, 3), "active": active}


def publish_sky(job: Any) -> Markup:
    """A constellation that lights up star by star as a release uploads, then settles when live."""
    prog = job_progress(job)
    if prog is None:
        return Markup("")
    pts = _points(str(getattr(job, "id", "")), 12, 360, 72, 10)
    lit = round(prog["fraction"] * len(pts))
    lines = "".join(
        f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}"/>'
        for a, b in zip(pts[: max(lit - 1, 0)], pts[1:lit], strict=False)
    )
    stars = "".join(
        f'<circle class="{"lit" if i < lit else "dim"}" style="--i:{i}" cx="{x}" cy="{y}" r="{r}"/>'
        for i, (x, y, r) in enumerate(pts)
    )
    state = "settled" if prog["active"] else "rising"
    label = (
        "Release is live"
        if prog["active"]
        else f"Uploading {prog['done']} of {prog['total']} files"
    )
    return Markup(
        f'<figure class="publish-sky {state}" data-lit="{lit}">'
        f'<svg viewBox="0 0 360 72" role="img" aria-label="{label}"><g class="links">{lines}</g>'
        f'<g class="stars">{stars}</g></svg><figcaption>{label}</figcaption></figure>'
    )
