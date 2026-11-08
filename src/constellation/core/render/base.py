"""Shared renderer types."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from constellation.core.markdown.document import Alternative, Ref, Warning

UrlFor = Callable[[Ref], str]

GENERIC_LINK_TEXT = {"here", "this", "link", "click here", "read more", "more", "source", "url", "this link", "this page"}
EMBED_TAGS = ("iframe", "video", "audio", "script", "style", "form", "embed", "object", "canvas")


@dataclass(slots=True)
class RenderResult:
    text: str
    warnings: list[Warning] = field(default_factory=list)


class WarningSink:
    """Collects warnings; ``once`` codes are emitted a single time per document."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.items: list[Warning] = []
        self._once: set[str] = set()

    def add(self, code: str, severity: str, line: int, message: str,
            alternatives: list[Alternative] | None = None, *, once: bool = False) -> None:
        if once:
            if code in self._once:
                return
            self._once.add(code)
        self.items.append(Warning(code, severity, self.platform, line, message, alternatives or []))  # type: ignore[arg-type]


_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"[ \t ]+")


def strip_tags(html: str) -> str:
    return _TAG_RX.sub("", html)


def collapse_ws(text: str) -> str:
    return _WS_RX.sub(" ", text).strip()


def is_generic_link_text(text: str, url: str) -> bool:
    t = text.strip().lower().rstrip(".!")
    if not t:
        return True
    if t in GENERIC_LINK_TEXT:
        return True
    return t == url.lower() or t == url.lower().split("://", 1)[-1]


def display_url(url: str) -> str:
    """A URL as a label: scheme dropped, trailing slash dropped."""
    u = url.split("://", 1)[-1] if "://" in url else url
    return u.rstrip("/") or url
