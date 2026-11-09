"""Document → Gopher: a gophermap (type 1 menu) per item with links as real items after each
block, and a plain-text file (type 0) with footnoted links. RFC 1436 wire rules: CRLF, TAB-separated
item fields, display strings under 70 columns, no tabs in display text, dot-stuffing in text."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from constellation.core.markdown.document import Alternative, Block, Document, Inline, inline_text
from constellation.core.models import RenderGopher
from constellation.core.render.base import (
    RenderResult,
    UrlFor,
    WarningSink,
    collapse_ws,
    display_url,
    strip_tags,
)

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def display_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_display(text: str, width: int) -> str:
    """Cut a string so it occupies at most ``width`` display columns."""
    out: list[str] = []
    used = 0
    for ch in text:
        w = (
            0
            if unicodedata.combining(ch)
            else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        )
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


def wrap(text: str, width: int, *, indent: str = "", first: str | None = None) -> list[str]:
    """Greedy word wrap on display width; a word longer than the width stays whole on its line."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = first if first is not None else indent
    current_len = display_width(current)
    started = False
    for word in words:
        w = display_width(word)
        if started and current_len + 1 + w > width:
            lines.append(current)
            current, current_len, started = indent + word, display_width(indent) + w, True
        elif started:
            current, current_len = current + " " + word, current_len + 1 + w
        else:
            current, current_len, started = current + word, current_len + w, True
    lines.append(current)
    return lines


@dataclass(slots=True)
class GopherTarget:
    host: str
    port: int = 70


@dataclass(slots=True)
class _Link:
    url: str
    label: str
    is_image: bool = False


def clean_display(text: str) -> str:
    return text.replace("\t", "    ").replace("\r", "").replace("\x00", "")


def item_line(item_type: str, display: str, selector: str, host: str, port: int) -> str:
    return f"{item_type}{clean_display(display)}\t{selector}\t{host}\t{port}"


def info_line(text: str, host: str, port: int) -> str:
    return item_line("i", text, "fake", host, port)


def item_for_url(url: str, label: str, target: GopherTarget, *, is_image: bool = False) -> str:
    """A menu item for a resolved URL: native selectors for this hole, h+URL: for everything else."""
    if "://" in url:
        return item_line("h", label, f"URL:{url}", target.host, target.port)
    selector = url
    if is_image or selector.lower().endswith(IMAGE_EXT):
        return item_line("I", label, selector, target.host, target.port)
    if selector.endswith(".txt"):
        return item_line("0", label, selector, target.host, target.port)
    if selector.endswith("/") or "." not in selector.rsplit("/", 1)[-1]:
        return item_line("1", label, selector.rstrip("/") or "/", target.host, target.port)
    return item_line("9", label, selector, target.host, target.port)


class GopherRenderer:
    """Produces the body lines of a gophermap and of a text file; templates add header and footer."""

    platform = "gopher"

    def __init__(self, cfg: RenderGopher, url_for: UrlFor, target: GopherTarget) -> None:
        self.cfg = cfg
        self.url_for = url_for
        self.target = target

    def render(self, doc: Document) -> RenderResult:
        """The gophermap body (wire lines, LF-joined; the writer adds CRLF)."""
        st = _State(self, doc, mode="map")
        st.blocks(doc.blocks)
        st.footnotes()
        return RenderResult("\n".join(st.lines) + ("\n" if st.lines else ""), st.warn.items)

    def render_text(self, doc: Document) -> RenderResult:
        """The plain-text body: links become numbered references listed at the end."""
        st = _State(self, doc, mode="text")
        st.blocks(doc.blocks)
        st.footnotes()
        st.flush_text_links()
        return RenderResult("\n".join(st.lines) + ("\n" if st.lines else ""), st.warn.items)


class _State:
    def __init__(self, r: GopherRenderer, doc: Document, *, mode: str) -> None:
        self.r = r
        self.doc = doc
        self.mode = mode
        self.width = r.cfg.width_map if mode == "map" else r.cfg.width_text
        self.lines: list[str] = []
        self.warn = WarningSink("gopher")
        self.text_links: list[_Link] = []

    # ---- emitters ------------------------------------------------------------------------
    def text(self, line: str) -> None:
        line = clean_display(line)
        if self.mode == "map":
            self.lines.append(info_line(line, self.r.target.host, self.r.target.port))
        else:
            self.lines.append("." + line if line.startswith(".") else line)

    def para(self, text: str, *, indent: str = "", first: str | None = None) -> None:
        for part in text.split("\n"):
            for line in wrap(collapse_ws(part), self.width, indent=indent, first=first):
                self.text(line)
            first = None

    def blank(self) -> None:
        if self.lines and self.lines[-1] not in (
            "",
            info_line("", self.r.target.host, self.r.target.port),
        ):
            self.text("")

    def links(self, links: list[_Link]) -> None:
        if not links:
            return
        if self.mode == "map":
            for link in links:
                self.lines.append(
                    item_for_url(
                        link.url,
                        truncate_display(link.label, self.width - 1),
                        self.r.target,
                        is_image=link.is_image,
                    )
                )
            self.text("")
        else:
            self.text_links.extend(links)

    def flush_text_links(self) -> None:
        if not self.text_links:
            return
        self.blank()
        self.text("Links:")
        for i, link in enumerate(self.text_links, 1):
            self.para(f"[{i}] {link.label}: {link.url}", indent="    ")

    # ---- inlines ---------------------------------------------------------------------------
    def inline(self, inlines: list[Inline], links: list[_Link]) -> str:
        parts: list[str] = []
        for n in inlines:
            k = n.kind
            if k == "text":
                parts.append(n.text)
            elif k == "soft_break":
                parts.append(" ")
            elif k == "hard_break":
                parts.append("\n")
            elif k in ("emphasis", "strong", "strike"):
                parts.append(self.inline(n.children, links))
            elif k == "code":
                parts.append(n.text)
            elif k == "link":
                assert n.ref is not None
                text = self.inline(n.children, links)
                if n.ref.kind == "fragment":
                    parts.append(text)
                    continue
                url = self.r.url_for(n.ref)
                label = collapse_ws(text) or display_url(url)
                links.append(_Link(url, label))
                if self.mode == "text":
                    parts.append(f"{text}[{len(self.text_links) + len(links)}]")
                else:
                    parts.append(text)
            elif k == "image":
                assert n.ref is not None
                url = self.r.url_for(n.ref)
                alt = n.text.strip() or "image"
                parts.append(f"[image: {alt}]")
                links.insert(0, _Link(url, alt, is_image=True))
            elif k == "footnote_ref":
                parts.append(f"[{n.text}]")
            elif k == "html":
                parts.append(strip_tags(n.text))
        return "".join(parts)

    # ---- blocks ----------------------------------------------------------------------------
    def blocks(self, blocks: list[Block]) -> None:
        for b in blocks:
            self.block(b)

    def block(self, b: Block) -> None:
        k = b.kind
        links: list[_Link] = []
        if k == "paragraph":
            real = [
                i
                for i in b.inlines
                if not (i.kind in ("text", "soft_break") and not i.text.strip())
            ]
            if len(real) == 1 and real[0].kind == "image":
                img = real[0]
                assert img.ref is not None
                alt = img.text.strip() or "image"
                self.text(f"[Image: {alt}]")
                self.links([_Link(self.r.url_for(img.ref), alt, is_image=True)])
                if img.title:
                    self.text(img.title)
                self.blank()
                return
            self.para(self.inline(b.inlines, links))
            self.links(links)
            self.blank()
        elif k == "heading":
            text = collapse_ws(self.inline(b.inlines, links).replace("\n", " "))
            self.blank()
            if b.level <= 2:
                self.para(text.upper())
            else:
                self.para(text)
                self.text("-" * min(display_width(text), self.width))
            self.links(links)
            self.blank()
        elif k == "list":
            self.list_block(b, links, depth=0)
            self.links(links)
            self.blank()
        elif k == "quote":
            for child in b.children:
                if child.kind == "paragraph":
                    self.para(self.inline(child.inlines, links), indent="    ", first='    "')
                    if self.lines:
                        self.lines[-1] = self.lines[-1] + '"'
                else:
                    self.block(child)
            self.links(links)
            self.blank()
        elif k == "code":
            self.blank()
            for raw in b.text.rstrip("\n").split("\n"):
                raw = raw.expandtabs(4)
                if display_width(raw) > self.width:
                    self.warn.add(
                        "PRE-TOO-WIDE",
                        "warn",
                        b.line[0],
                        f"Preformatted line is {display_width(raw)} columns; Gopher clients may truncate beyond {self.width}.",
                    )
                self.text("    " + raw if self.mode == "text" else raw)
            self.blank()
        elif k == "table":
            self.warn.add(
                "TABLE-RECORDS", "info", b.line[0], "Table is shown as a record list on Gopher."
            )
            if not b.rows:
                return
            header = [collapse_ws(self.inline(c, links)) for c in b.rows[0]]
            for row in b.rows[1:]:
                cells = [collapse_ws(self.inline(c, links)) for c in row]
                for i, cell in enumerate(cells):
                    label = header[i] if i < len(header) else f"Column {i + 1}"
                    self.para(f"{label}: {cell}", indent="    ", first="  * " if i == 0 else "    ")
            self.links(links)
            self.blank()
        elif k == "hr":
            self.blank()
            self.text("- - -")
            self.blank()
        elif k == "html":
            text = collapse_ws(strip_tags(b.text))
            if text:
                self.warn.add(
                    "HTML-STRIPPED", "info", b.line[0], "HTML block converted to plain text."
                )
                self.para(text)
                self.blank()
        elif k == "container":
            if b.info == "gopher":
                for raw in b.text.split("\n"):
                    self.text(raw.rstrip())
                self.blank()
        elif k == "deflist":
            term = ""
            for child in b.children:
                if child.kind == "def_term":
                    term = collapse_ws(self.inline(child.inlines, links))
                elif child.kind == "def_desc":
                    desc = " ".join(
                        collapse_ws(self.inline(c.inlines, links))
                        for c in child.children
                        if c.kind == "paragraph"
                    )
                    self.para(f"{term}: {desc}", indent="    ", first="  * ")
            self.links(links)
            self.blank()

    def list_block(self, b: Block, links: list[_Link], *, depth: int) -> None:
        number = b.start
        for item in b.children:
            marker = f"{number}. " if b.ordered else "* "
            if item.task:
                marker += "[x] " if item.task == "done" else "[ ] "
            first = True
            for child in item.children:
                if child.kind == "paragraph":
                    text = self.inline(child.inlines, links)
                    indent = "  " * (depth + 1) + " " * len(marker)
                    self.para(
                        text,
                        indent=indent,
                        first=("  " * (depth + 1) + marker) if first else indent,
                    )
                    first = False
                elif child.kind == "list":
                    if first:
                        self.text("  " * (depth + 1) + marker.rstrip())
                        first = False
                    self.list_block(child, links, depth=depth + 1)
                else:
                    self.block(child)
            number += 1

    def footnotes(self) -> None:
        if not self.doc.footnote_order:
            return
        self.blank()
        self.text("Notes")
        self.text("-----")
        for key in self.doc.footnote_order:
            links: list[_Link] = []
            texts = [
                collapse_ws(self.inline(c.inlines, links))
                for c in self.doc.footnotes[key]
                if c.kind == "paragraph"
            ]
            self.para(f"[{key}] " + " ".join(t for t in texts if t), indent="    ")
            self.links(links)
        self.blank()


def encode_selector(path: str) -> str:
    return quote(path, safe="/:._-~%+")


def unused_alternative() -> (
    Alternative
):  # pragma: no cover - keeps the import meaningful for plugins
    return Alternative("")


def inline_plain(inlines: list[Inline]) -> str:
    return inline_text(inlines)
