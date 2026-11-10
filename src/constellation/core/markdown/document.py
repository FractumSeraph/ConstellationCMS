"""The platform-independent document model.

A Markdown source is parsed once into a :class:`Document`; every renderer reads the same tree.
Blocks remember their source line range so warnings can point at the offending line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RefKind = Literal[
    "post",
    "page",
    "tag",
    "category",
    "series",
    "author",
    "media",
    "external",
    "fragment",
    "home",
    "index",
]
Severity = Literal["info", "warn", "error"]


@dataclass(slots=True)
class Ref:
    """A link or image target, resolved to a concrete URL per platform at render time."""

    kind: RefKind
    target: str
    fragment: str | None = None
    raw: str = ""

    def with_fragment(self, fragment: str | None) -> Ref:
        return Ref(self.kind, self.target, fragment, self.raw)


@dataclass(slots=True)
class Inline:
    """A run of inline content inside a block."""

    kind: Literal[
        "text",
        "emphasis",
        "strong",
        "code",
        "strike",
        "link",
        "image",
        "footnote_ref",
        "hard_break",
        "soft_break",
        "html",
    ]
    text: str = ""
    ref: Ref | None = None
    title: str | None = None
    children: list[Inline] = field(default_factory=list)


@dataclass(slots=True)
class Block:
    """A block-level element. ``line`` is the 1-based inclusive source line range."""

    kind: Literal[
        "paragraph",
        "heading",
        "list",
        "list_item",
        "quote",
        "code",
        "table",
        "hr",
        "html",
        "container",
        "gallery",
        "deflist",
        "def_term",
        "def_desc",
    ]
    line: tuple[int, int] = (0, 0)
    level: int = 0
    ordered: bool = False
    start: int = 1
    info: str = ""
    text: str = ""
    inlines: list[Inline] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)
    rows: list[list[list[Inline]]] = field(default_factory=list)
    aligns: list[str] = field(default_factory=list)
    task: Literal["todo", "done"] | None = None


@dataclass(slots=True)
class Document:
    blocks: list[Block] = field(default_factory=list)
    footnotes: dict[str, list[Block]] = field(default_factory=dict)
    footnote_order: list[str] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)


@dataclass(slots=True)
class Alternative:
    """A concrete alternative the editor can offer for a lossy construct."""

    label: str
    detail: str = ""


@dataclass(slots=True)
class Warning:
    code: str
    severity: Severity
    platform: str
    line: int
    message: str
    alternatives: list[Alternative] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "platform": self.platform,
            "line": self.line,
            "message": self.message,
            "alternatives": [a.label for a in self.alternatives],
        }


def inline_text(inlines: list[Inline]) -> str:
    """The plain text of a list of inlines, markup dropped."""
    out: list[str] = []
    for node in inlines:
        if node.kind in ("text", "code", "html"):
            out.append(node.text)
        elif node.kind == "hard_break":
            out.append("\n")
        elif node.kind == "soft_break":
            out.append(" ")
        elif node.kind == "image":
            out.append(node.text)
        elif node.kind == "footnote_ref":
            pass
        else:
            out.append(inline_text(node.children))
    return "".join(out)


def walk_inlines(inlines: list[Inline]) -> list[Inline]:
    """All inlines in document order, depth first."""
    out: list[Inline] = []
    for node in inlines:
        out.append(node)
        out.extend(walk_inlines(node.children))
    return out


def walk_blocks(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for b in blocks:
        out.append(b)
        out.extend(walk_blocks(b.children))
    return out


def gallery_images(block: Block) -> list[Inline]:
    """The images inside a ``gallery`` block, in order."""
    out: list[Inline] = []
    for b in walk_blocks(block.children):
        out.extend(i for i in walk_inlines(b.inlines) if i.kind == "image")
    return out


def gallery_extras(block: Block) -> bool:
    """True when a gallery holds something other than images (it is dropped on every platform)."""
    for b in walk_blocks(block.children):
        for i in b.inlines:
            if i.kind == "image" or (
                i.kind in ("text", "soft_break", "hard_break") and not i.text.strip()
            ):
                continue
            return True
        if b.kind != "paragraph":
            return True
    return False
