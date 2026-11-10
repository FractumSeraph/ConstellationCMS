"""The editor's visual mode: Markdown → editable HTML blocks, and edited blocks → Markdown.

The file on disk stays Markdown. The body is split into its top-level blocks (paragraphs,
headings, lists, quotes, tables, rules). Blocks made only of what the visual editor can show
and write back faithfully are sent as HTML to edit; everything else (code, raw HTML, galleries,
platform-only blocks, footnotes, wiki links, shortcodes) is sent as a locked block that keeps its
source exactly. Blocks the user did not touch are written back as their original source, so
switching modes never reformats a file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from markdown_it.tree import SyntaxTreeNode

from constellation.core.markdown.document import Ref
from constellation.core.markdown.parse import parse_markdown, parser
from constellation.core.render.html import HtmlRenderer
from constellation.importers.html_to_md import html_to_markdown

EDITABLE_BLOCKS = {
    "paragraph",
    "heading",
    "bullet_list",
    "ordered_list",
    "blockquote",
    "hr",
    "table",
}
SIMPLE_INLINES = {
    "text",
    "em",
    "strong",
    "s",
    "code_inline",
    "link",
    "image",
    "softbreak",
    "hardbreak",
}
# Inside lists, quotes and tables, these containers may appear around simple inline content.
CONTAINER_NODES = {
    "list_item",
    "paragraph",
    "bullet_list",
    "ordered_list",
    "blockquote",
    "inline",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "heading",
}


def _simple(node: SyntaxTreeNode) -> bool:
    """True when every descendant is something the visual editor round-trips."""
    for child in node.children:
        t = child.type
        if t in SIMPLE_INLINES:
            if t == "image" and child.attrs.get("title"):
                return False  # a caption becomes a figcaption, which would not come back as a title
            if child.children and not _simple(child):
                return False
            continue
        if t in CONTAINER_NODES:
            if t in ("bullet_list", "ordered_list") and any(
                li.type == "list_item" and _is_task(li) for li in child.children
            ):
                return False
            if not _simple(child):
                return False
            continue
        return False
    return True


def _has_task(node: SyntaxTreeNode) -> bool:
    """True when this block or anything inside it is a task list item (checkboxes are kept as text)."""
    if node.type == "list_item" and _is_task(node):
        return True
    return any(_has_task(c) for c in node.children)


def _is_task(li: SyntaxTreeNode) -> bool:
    first = li.children[0] if li.children else None
    if first is None or first.type != "paragraph":
        return False
    text = first.children[0].content if first.children else ""
    return text.lstrip().startswith(("[ ]", "[x]", "[X]"))


def to_blocks(body: str, media_url: Callable[[Ref], str]) -> dict[str, Any]:
    """Split ``body`` into blocks for the visual editor.

    Returns ``{"blocks": [{"kind": "edit" | "locked", "src": ..., "html": ...}], "media": {raw: url}}``
    where ``media`` maps each image address as written to a URL the editor can display.
    """
    lines = body.split("\n")
    tree = SyntaxTreeNode(parser().parse(body))
    spans: list[tuple[int, int, SyntaxTreeNode | None]] = []
    for top in tree.children:
        if top.map is None:
            continue
        start, end = top.map
        # A ``:::`` container's map stops before its closing marker; keep the marker with it.
        closing = end < len(lines) and lines[end].strip().startswith(":::")
        if top.type.startswith("container_") and closing:
            end += 1
        spans.append((start, end, top))
    # Lines no top-level node claims (footnote definitions, for example) stay as locked blocks.
    covered = [False] * len(lines)
    for start, end, _ in spans:
        for i in range(start, min(end, len(lines))):
            covered[i] = True
    i = 0
    while i < len(lines):
        if not covered[i] and lines[i].strip():
            j = i
            while j < len(lines) and not covered[j] and lines[j].strip():
                j += 1
            spans.append((i, j, None))
            i = j
        else:
            i += 1
    spans.sort(key=lambda s: s[0])

    media: dict[str, str] = {}
    blocks: list[dict[str, str]] = []
    for n, (start, end, node) in enumerate(spans):
        src = "\n".join(lines[start:end]).rstrip()
        if not src.strip():
            continue
        # The original spacing before the next block, so untouched files come back unchanged.
        nxt = spans[n + 1][0] if n + 1 < len(spans) else len(lines)
        # Some blocks (lists) claim the blank lines after them; count from the last real line.
        real_end = start + src.count("\n") + 1
        blank = sum(1 for ln in lines[real_end:nxt] if not ln.strip())
        sep = "\n" * (blank + 1) if n + 1 < len(spans) else "\n\n"
        editable = (
            node is not None
            and node.type in EDITABLE_BLOCKS
            and _simple(node)
            and not _has_task(node)
            and "[[" not in src
            and "{{<" not in src
            and "[^" not in src
        )
        if not editable:
            blocks.append({"kind": "locked", "src": src, "html": "", "sep": sep})
            continue
        doc = parse_markdown(src, resolve=_keep_raw)
        for ref in doc.refs:
            if ref.kind == "media" or (ref.raw and _looks_like_image(ref.raw)):
                media[ref.raw] = media_url(ref)
        html = HtmlRenderer(lambda r: r.raw or r.target).render(doc).text.strip()
        blocks.append({"kind": "edit", "src": src, "html": html, "sep": sep})
    stripped = body.strip("\n")
    lead = body[: len(body) - len(body.lstrip("\n"))] if stripped else ""
    tail = body[len(body.rstrip("\n")) :] if stripped else ""
    return {"blocks": blocks, "media": media, "lead": lead, "tail": tail}


def _keep_raw(href: str, is_image: bool) -> Ref:
    from constellation.core.routes import RefResolver

    ref = RefResolver().resolve(href, is_image=is_image)
    return ref if ref.raw else Ref(ref.kind, ref.target, ref.fragment, href)


def _looks_like_image(href: str) -> bool:
    return href.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))


def to_markdown(items: list[dict[str, Any]], *, lead: str = "", tail: str = "\n") -> str:
    """Join edited blocks back into Markdown: ``{"src": ...}`` is kept, ``{"html": ...}`` converted.

    ``lead`` and ``tail`` are the blank lines the original body started and ended with (line
    breaks only), so an untouched body comes back exactly.
    """
    lead = lead if set(lead) <= {"\n"} else ""
    tail = tail if set(tail) <= {"\n"} else "\n"
    out = ""
    last_sep = ""
    for item in items:
        if "src" in item and item["src"] is not None:
            text = str(item["src"]).rstrip()
        else:
            text = html_to_markdown(str(item.get("html", ""))).strip()
        if not text:
            continue
        out += last_sep + text
        sep = str(item.get("sep") or "\n\n")
        # Only line breaks are accepted as a separator; anything else becomes one blank line.
        last_sep = (
            sep if sep.strip() == "" and sep.count("\n") >= 1 and set(sep) == {"\n"} else "\n\n"
        )
    return lead + out + tail if out else ""
