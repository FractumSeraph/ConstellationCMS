"""Document → gemtext (text/gemini), with warnings and alternatives for lossy constructs.

The mapping follows ``docs/plan/04-rendering.md``. Every emitted text line goes through
:meth:`_text_line`, which defuses lines that would otherwise read as gemtext markup.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from constellation.core.markdown.document import (
    Alternative,
    Block,
    Document,
    Inline,
    Ref,
    gallery_images,
    inline_text,
)
from constellation.core.models import RenderGemini
from constellation.core.render.base import (
    EMBED_TAGS,
    RenderResult,
    UrlFor,
    WarningSink,
    collapse_ws,
    display_url,
    is_generic_link_text,
    strip_tags,
)

RESERVED_PREFIXES = ("=>", "#", "* ", ">", "```")
BLOCK_TAGS = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section")
CTRL_RX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ]|\x1b\[[0-9;]*[A-Za-z]")


class _Link:
    __slots__ = ("url", "label", "marker")

    def __init__(self, url: str, label: str, marker: str = "") -> None:
        self.url, self.label, self.marker = url, label, marker


class GemtextRenderer:
    platform = "gemini"

    def __init__(self, cfg: RenderGemini, url_for: UrlFor) -> None:
        self.cfg = cfg
        self.url_for = url_for

    def render(self, doc: Document) -> RenderResult:
        state = _State(self.cfg, self.url_for, doc)
        state.blocks(doc.blocks, prefix="")
        if self.cfg.link_placement == "at-end" and state.end_links:
            state.flush_end_links()
        if doc.footnote_order:
            state.footnotes()
        return RenderResult(state.output(), state.warn.items)


class _State:
    def __init__(self, cfg: RenderGemini, url_for: UrlFor, doc: Document) -> None:
        self.cfg = cfg
        self.url_for = url_for
        self.doc = doc
        self.lines: list[str] = []
        self.warn = WarningSink("gemini")
        self.end_links: list[_Link] = []
        self.seen_urls: set[str] = set()
        self.deferred: list[Block] = []  # code blocks lifted out of lists/quotes
        self.marker_count = 0

    # ---- output helpers -------------------------------------------------------------------
    def emit(self, line: str = "") -> None:
        self.lines.append(line.rstrip())

    def blank(self) -> None:
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    def text_line(self, text: str, line_no: int, *, prefix: str = "") -> None:
        """Emit one text line, defusing reserved prefixes and control characters."""
        if CTRL_RX.search(text):
            text = CTRL_RX.sub("", text)
            self.warn.add(
                "CTRL-CHAR", "warn", line_no, "Control characters were removed from this line."
            )
        text = text.rstrip()
        if not prefix and text.startswith(RESERVED_PREFIXES):
            text = " " + text
            self.warn.add(
                "PREFIX-ESCAPED",
                "info",
                line_no,
                f"Line started with {text.strip()[:2]!r}; a space was added so it is not read as markup.",
            )
        self.emit(prefix + text if text else prefix.rstrip())

    def output(self) -> str:
        out: list[str] = []
        for line in self.lines:
            if line == "" and out and out[-1] == "":
                continue
            out.append(line)
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n" if out else ""

    # ---- inline conversion ----------------------------------------------------------------
    def inline(
        self, inlines: list[Inline], links: list[_Link], line_no: int, *, context: str = ""
    ) -> str:
        parts: list[str] = []
        keep = self.cfg.inline_markup == "markdown"
        for node in inlines:
            k = node.kind
            if k == "text":
                parts.append(node.text)
            elif k == "soft_break":
                parts.append(" ")
            elif k == "hard_break":
                parts.append("\n")
            elif k == "emphasis":
                inner = self.inline(node.children, links, line_no, context=context)
                parts.append(f"*{inner}*" if keep else inner)
                self._style_note(line_no)
            elif k == "strong":
                inner = self.inline(node.children, links, line_no, context=context)
                parts.append(f"**{inner}**" if keep else inner)
                self._style_note(line_no)
            elif k == "code":
                parts.append(f"`{node.text}`" if keep else node.text)
                self._style_note(line_no)
            elif k == "strike":
                inner = self.inline(node.children, links, line_no, context=context)
                parts.append(inner)
                self.warn.add(
                    "STRIKE-LOST",
                    "warn",
                    line_no,
                    "Struck-out text will read as normal text on Gemini.",
                    [Alternative("Write it as [struck: text]"), Alternative("Reword it")],
                )
            elif k == "link":
                text = self.inline(node.children, links, line_no, context=context)
                assert node.ref is not None
                url = self.url_for(node.ref)
                if node.ref.kind == "fragment":
                    self.warn.add(
                        "LINK-FRAGMENT-SAME-PAGE",
                        "info",
                        line_no,
                        "Same-page anchor links are not available on Gemini; the text is kept.",
                    )
                    parts.append(text)
                    continue
                label = collapse_ws(text) or display_url(url)
                if is_generic_link_text(text, url):
                    if text.strip() and not text.strip().startswith(("http", "gemini", "www.")):
                        self.warn.add(
                            "LINK-GENERIC-TEXT",
                            "warn",
                            line_no,
                            f"Link text {text.strip()!r} will be the only label on Gemini. Use descriptive link text.",
                        )
                        ctx = _sentence_with(collapse_ws(context), text.strip())
                        if ctx:
                            label = (
                                f"{label}: {ctx[:60].rstrip()}…"
                                if len(ctx) > 60
                                else f"{label}: {ctx}"
                            )
                    else:
                        label = display_url(url)
                if node.title:
                    label = f"{label} — {node.title}"
                    self.warn.add(
                        "LINK-TITLE-LOST",
                        "info",
                        line_no,
                        "Link titles have no slot on Gemini; the title was appended to the label.",
                    )
                marker = ""
                if self.cfg.link_markers != "none":
                    self.marker_count += 1
                    marker = (
                        f"[{self.marker_count}]"
                        if self.cfg.link_markers == "numbers"
                        else f"[{chr(96 + self.marker_count)}]"
                    )
                    text = text + marker
                    label = f"{marker} {label}"
                parts.append(text)
                self._add_link(links, _Link(url, label, marker))
            elif k == "image":
                assert node.ref is not None
                url = self.url_for(node.ref)
                alt = node.text.strip()
                parts.append(f"[image: {alt}]" if alt else "[image]")
                links.insert(0, _Link(url, alt or "image"))
                self.warn.add(
                    "IMG-INLINE",
                    "info",
                    line_no,
                    "Inline image moved below the paragraph.",
                    [Alternative("Move the image to its own paragraph")],
                )
                if not alt:
                    self._no_alt(line_no)
            elif k == "footnote_ref":
                parts.append(f"[{node.text}]")
            elif k == "html":
                parts.append(self._inline_html(node.text, links, line_no))
        return "".join(parts)

    def _style_note(self, line_no: int) -> None:
        if self.cfg.inline_markup == "markdown":
            self.warn.add(
                "INLINE-STYLE-KEPT",
                "info",
                line_no,
                "Bold, italic and code markers are kept as plain * ** ` characters on Gemini.",
                once=True,
            )
        else:
            self.warn.add(
                "INLINE-STYLE-LOST",
                "info",
                line_no,
                "Bold, italic and code spans appear as plain text on Gemini.",
                once=True,
            )

    def _no_alt(self, line_no: int) -> None:
        self.warn.add(
            "IMG-NO-ALT",
            "warn",
            line_no,
            "Image has no alt text. Gemini and Gopher readers will see an empty description.",
            [Alternative("Add alt text")],
        )

    def _inline_html(self, raw: str, links: list[_Link], line_no: int) -> str:
        m = re.match(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", raw, re.I | re.S)
        if m:
            label = collapse_ws(strip_tags(m.group(2))) or display_url(m.group(1))
            self._add_link(links, _Link(m.group(1), label))
            return label
        if re.match(r"<sup>", raw, re.I):
            return "^"
        text = strip_tags(raw)
        if raw.strip() and text != raw:
            self.warn.add(
                "INLINE-HTML-STRIPPED",
                "info",
                line_no,
                "Inline HTML tags were removed; the text is kept.",
                once=True,
            )
        return text

    def _add_link(self, links: list[_Link], link: _Link) -> None:
        if self.cfg.dedupe_links == "block" and any(existing.url == link.url for existing in links):
            return
        if self.cfg.dedupe_links == "document":
            if link.url in self.seen_urls:
                return
            self.seen_urls.add(link.url)
        links.append(link)

    def flush_links(self, links: list[_Link]) -> None:
        if not links:
            return
        if self.cfg.link_placement == "at-end":
            self.end_links.extend(links)
            return
        for link in links:
            self.emit(f"=> {_encode_url(link.url)} {link.label}".rstrip())
        self.blank()

    def flush_end_links(self) -> None:
        self.blank()
        self.emit("### Links")
        seen: set[str] = set()
        for link in self.end_links:
            if link.url in seen:
                continue
            seen.add(link.url)
            self.emit(f"=> {_encode_url(link.url)} {link.label}".rstrip())
        self.blank()

    # ---- blocks -----------------------------------------------------------------------------
    def blocks(self, blocks: list[Block], *, prefix: str) -> None:
        for block in blocks:
            self.block(block, prefix=prefix)
            if self.deferred and not prefix:
                deferred, self.deferred = self.deferred, []
                for d in deferred:
                    self.block(d, prefix="")

    def block(self, b: Block, *, prefix: str) -> None:
        kind = b.kind
        line_no = b.line[0]
        if kind == "paragraph":
            self.paragraph(b, prefix)
        elif kind == "heading":
            self.heading(b, prefix)
        elif kind == "list":
            self.list_block(b, prefix)
        elif kind == "quote":
            self.quote(b, prefix)
        elif kind == "code":
            self.code(b, prefix)
        elif kind == "table":
            self.table(b)
        elif kind == "hr":
            self.blank()
            if self.cfg.hr:
                self.text_line(self.cfg.hr, line_no)
                self.blank()
        elif kind == "html":
            self.html_block(b)
        elif kind == "gallery":
            for img in gallery_images(b):
                self.image_block(img, line_no)
        elif kind == "container":
            if b.info == "gemini":
                for raw in b.text.split("\n"):
                    self.emit(raw.rstrip())
                self.blank()
        elif kind == "deflist":
            self.deflist(b)
        else:
            self.text_line(inline_text(b.inlines), line_no, prefix=prefix)

    def paragraph(self, b: Block, prefix: str) -> None:
        line_no = b.line[0]
        if _is_image_paragraph(b):
            self.image_block(b.inlines[0], line_no)
            return
        links: list[_Link] = []
        text = self.inline(b.inlines, links, line_no, context=inline_text(b.inlines))
        if not prefix and _is_link_paragraph(b) and len(links) == 1:
            # The link line already carries the text; printing it again reads as an echo.
            self.flush_links(links)
            self.blank()
            return
        for part in text.split("\n"):
            part = collapse_ws(part)
            if part or prefix:
                self.text_line(part, line_no, prefix=prefix)
        if not prefix:
            self.flush_links(links)
            self.blank()
        else:
            self.flush_links(links)

    def image_block(self, img: Inline, line_no: int) -> None:
        assert img.ref is not None
        url = self.url_for(img.ref)
        alt = img.text.strip()
        if not alt:
            self._no_alt(line_no)
        if img.ref.kind == "external":
            self.warn.add(
                "IMG-EXTERNAL",
                "info",
                line_no,
                "Image is hosted elsewhere; the link line points there.",
            )
        self.text_line(self.cfg.image_notice.format(alt=alt), line_no)
        if self.cfg.image_link:
            self.emit(f"=> {_encode_url(url)} {alt or 'image'}")
        if img.title:
            self.text_line(img.title, line_no)
        self.blank()

    def heading(self, b: Block, prefix: str) -> None:
        line_no = b.line[0]
        links: list[_Link] = []
        text = collapse_ws(self.inline(b.inlines, links, line_no).replace("\n", " "))
        level = b.level
        if level == 1:
            self.warn.add(
                "H1-IN-BODY",
                "warn",
                line_no,
                "Body contains a level-1 heading; the post title is already the H1. It will be shown as a level-2 heading.",
                [Alternative("Change # to ##")],
            )
            level = 2
        self.blank()
        if prefix:
            self.text_line(text, line_no, prefix=prefix)
        elif level <= 3:
            self.emit(f"{'#' * level} {text}".rstrip())
        else:
            self.warn.add(
                "HEADING-TOO-DEEP",
                "warn",
                line_no,
                f"Level-{b.level} headings do not exist on Gemini; this one will be shown as a plain text line.",
                [
                    Alternative("Promote to ###"),
                    Alternative("Show in UPPER CASE"),
                    Alternative("Show as — text —"),
                ],
            )
            if self.cfg.heading_overflow == "promote":
                self.emit(f"### {text}".rstrip())
            elif self.cfg.heading_overflow == "upper":
                self.text_line(text.upper(), line_no)
            else:
                self.text_line(text, line_no)
        self.flush_links(links)
        self.blank()

    def list_block(self, b: Block, prefix: str) -> None:
        line_no = b.line[0]
        links: list[_Link] = []
        if not b.ordered and self._all_single_links(b):
            # A list of links becomes link lines, no bullets.
            for item in b.children:
                para = item.children[0]
                link = next(i for i in para.inlines if i.kind == "link")
                assert link.ref is not None
                label = collapse_ws(inline_text(link.children)) or display_url(
                    self.url_for(link.ref)
                )
                self.emit(f"{prefix}=> {_encode_url(self.url_for(link.ref))} {label}")
            self.blank()
            return
        if b.ordered:
            self.warn.add(
                "LIST-ORDERED",
                "info",
                line_no,
                "Numbered lists are shown as numbered text lines (not bullets).",
                [Alternative("Use bullets with numbers inside: * 1. item")],
            )
        self._list_items(b, prefix, links, depth=0)
        self.flush_links(links)
        self.blank()

    def _all_single_links(self, b: Block) -> bool:
        for item in b.children:
            if len(item.children) != 1 or item.children[0].kind != "paragraph" or item.task:
                return False
            inl = [
                i for i in item.children[0].inlines if not (i.kind == "text" and not i.text.strip())
            ]
            if len(inl) != 1 or inl[0].kind != "link":
                return False
        return bool(b.children)

    def _list_items(self, b: Block, prefix: str, links: list[_Link], *, depth: int) -> None:
        number = b.start
        for item in b.children:
            item_links: list[_Link] = [] if self.cfg.link_placement == "per-item" else links
            first = True
            for child in item.children:
                if child.kind == "paragraph":
                    text = self.inline(
                        child.inlines, item_links, child.line[0], context=inline_text(child.inlines)
                    )
                    parts = [collapse_ws(p) for p in text.split("\n")]
                    parts = [p for p in parts if p] or [""]
                    for i, part in enumerate(parts):
                        if first and i == 0:
                            self._bullet(part, b, number, item, depth, prefix, child.line[0])
                        else:
                            if i > 0 and first:
                                self.warn.add(
                                    "LIST-ITEM-BREAK",
                                    "info",
                                    child.line[0],
                                    "Line break inside a list item becomes an indented text line.",
                                )
                            self.text_line(part, child.line[0], prefix=prefix + "  " * (depth + 1))
                    if not first:
                        self.warn.add(
                            "LIST-ITEM-BLOCK",
                            "warn",
                            child.line[0],
                            "List item contains several paragraphs; only the first stays on the bullet.",
                            [Alternative("Split into separate lists")],
                        )
                    first = False
                elif child.kind == "list":
                    if first:
                        self._bullet("", b, number, item, depth, prefix, child.line[0])
                        first = False
                    self.warn.add(
                        "LIST-NESTED",
                        "warn",
                        child.line[0],
                        "Nested lists are not supported on Gemini; sub-items will be shown as indented plain text.",
                        [
                            Alternative("Flatten with a → prefix"),
                            Alternative("Write as a sentence"),
                        ],
                    )
                    if self.cfg.nested_lists == "sentence":
                        texts = [
                            collapse_ws(self.inline(gc.inlines, item_links, gc.line[0]))
                            for sub in child.children
                            for gc in sub.children
                            if gc.kind == "paragraph"
                        ]
                        if self.lines:
                            self.lines[-1] = self.lines[-1].rstrip() + ": " + "; ".join(texts)
                    else:
                        self._list_items(child, prefix, item_links, depth=depth + 1)
                elif child.kind == "code":
                    self.warn.add(
                        "LIST-ITEM-BLOCK",
                        "warn",
                        child.line[0],
                        "Code block inside a list item is moved after the list.",
                    )
                    self.deferred.append(child)
                else:
                    sub_text = collapse_ws(inline_text(child.inlines)) if child.inlines else ""
                    if sub_text:
                        self.text_line(sub_text, child.line[0], prefix=prefix + "  " * (depth + 1))
            if self.cfg.link_placement == "per-item":
                self.flush_links(item_links)
            number += 1

    def _bullet(
        self, text: str, lst: Block, number: int, item: Block, depth: int, prefix: str, line_no: int
    ) -> None:
        task = ""
        if item.task:
            task = "[x] " if item.task == "done" else "[ ] "
        if depth > 0:
            indent = "  " * depth
            if self.cfg.nested_lists == "flatten":
                self.emit(f"{prefix}* → {task}{text}".rstrip())
            else:
                self.text_line(
                    f"* {task}{text}" if not lst.ordered else f"{number}. {task}{text}",
                    line_no,
                    prefix=prefix + indent,
                )
            return
        if lst.ordered:
            if self.cfg.ordered_lists == "bullet-numbered":
                self.emit(f"{prefix}* {number}. {task}{text}".rstrip())
            else:
                self.text_line(f"{number}. {task}{text}", line_no, prefix=prefix)
        else:
            self.emit(f"{prefix}* {task}{text}".rstrip())

    def quote(self, b: Block, prefix: str) -> None:
        line_no = b.line[0]
        if prefix.startswith(">"):
            self.warn.add(
                "QUOTE-NESTED",
                "warn",
                line_no,
                "Nested quotes are flattened on Gemini.",
                [Alternative("Keep a literal >> marker")],
            )
            self.blocks(b.children, prefix=prefix)
            return
        links: list[_Link] = []
        self._quote_body(b.children, links, first=True)
        self.flush_links(links)
        self.blank()

    def _quote_body(self, children: list[Block], links: list[_Link], *, first: bool) -> bool:
        for child in children:
            if child.kind == "paragraph":
                if not first:
                    self.emit(">")
                text = self.inline(
                    child.inlines, links, child.line[0], context=inline_text(child.inlines)
                )
                for part in text.split("\n"):
                    part = collapse_ws(part)
                    self.emit(f"> {part}" if part else ">")
            elif child.kind == "code":
                self.warn.add(
                    "QUOTE-CODE",
                    "warn",
                    child.line[0],
                    "Code block inside a quote interrupts the quote on Gemini.",
                )
                self.blank()
                self.code(child, "")
            elif child.kind == "quote":
                self.warn.add(
                    "QUOTE-NESTED",
                    "warn",
                    child.line[0],
                    "Nested quotes are flattened on Gemini.",
                    [Alternative("Keep a literal >> marker")],
                )
                first = self._quote_body(child.children, links, first=first)
                continue
            elif child.kind in ("list", "heading"):
                self.warn.add(
                    "QUOTE-BLOCK-CONTENT",
                    "info",
                    child.line[0],
                    "Lists and headings inside a quote are shown as quoted text.",
                )
                if child.kind == "heading":
                    self.emit("> " + collapse_ws(self.inline(child.inlines, links, child.line[0])))
                else:
                    self._list_items(child, "> ", links, depth=0)
            else:
                self.block(child, prefix="> ")
            first = False
        return first

    def code(self, b: Block, prefix: str) -> None:
        line_no = b.line[0]
        info = b.info.split()[0] if b.info else ""
        if not info:
            if b.info == "" and not b.text.strip().startswith(("+", "|", "/", "\\")):
                self.warn.add(
                    "PRE-NO-ALT",
                    "info",
                    line_no,
                    "Preformatted block has no alt text; screen-reader users hear nothing useful.",
                    [Alternative("Add an info string after the opening fence")],
                )
            info = "code" if b.info == "" else ""
        self.blank()
        self.emit(f"```{info}".rstrip())
        for raw in b.text.rstrip("\n").split("\n"):
            raw = raw.rstrip()
            if raw.startswith("```"):
                self.warn.add(
                    "PRE-FENCE-INSIDE",
                    "error",
                    line_no,
                    "A line inside this code block starts with ``` which cannot be represented in gemtext; it was indented by one space.",
                )
                raw = " " + raw
            self.emit(raw)
        self.emit("```")
        self.blank()

    def table(self, b: Block) -> None:
        line_no = b.line[0]
        if not b.rows:
            return
        links: list[_Link] = []
        cells: list[list[str]] = []
        saved_markers = self.cfg.link_markers
        for row in b.rows:
            rendered_row: list[str] = []
            for cell in row:
                cell_links: list[_Link] = []
                text = collapse_ws(self.inline(cell, cell_links, line_no).replace("\n", " "))
                for cl in cell_links:
                    if saved_markers == "none":
                        n = len(links) + 1
                        cl.marker = f"[{n}]"
                        cl.label = f"[{n}] {cl.label}"
                        text = f"{text} [{n}]"
                    links.append(cl)
                rendered_row.append(text)
            cells.append(rendered_row)
        header, body = cells[0], cells[1:]
        ncols = max(len(r) for r in cells)
        for r in cells:
            r.extend([""] * (ncols - len(r)))
        widths = [max(len(r[i]) for r in cells) for i in range(ncols)]
        long_cells = any(len(c) > self.cfg.long_cell_chars for r in cells for c in r)
        total = sum(widths) + 2 * (ncols - 1)
        mode = self.cfg.tables
        if mode == "auto":
            mode = "records" if (long_cells or total > self.cfg.wide_table_cols) else "pre"
        if mode == "pre":
            self.warn.add(
                "TABLE-PRE",
                "info",
                line_no,
                "Table is shown as preformatted text; it will not wrap on narrow screens.",
                [Alternative("Render as a record list")],
            )
            if long_cells or total > self.cfg.wide_table_cols:
                self.warn.add(
                    "TABLE-LONG-CELLS",
                    "warn",
                    line_no,
                    "This table has long cells and will be very wide on Gemini. Consider the record-list rendering.",
                    [Alternative("Render as a record list")],
                )
            self.blank()
            self.emit("```table: " + ", ".join(h for h in header if h))
            self.emit("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)).rstrip())
            self.emit("  ".join("-" * widths[i] for i in range(ncols)))
            for r in body:
                self.emit("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
            self.emit("```")
            self.blank()
        else:
            self.warn.add(
                "TABLE-RECORDS",
                "info",
                line_no,
                "Table is shown as a record list on Gemini (one entry per row).",
                [Alternative("Render as preformatted text")],
            )
            for r in body:
                if ncols == 2:
                    self.emit(f"* {r[0]} — {r[1]}".rstrip(" —"))
                    continue
                first = True
                for i, c in enumerate(r):
                    label = header[i] if i < len(header) and header[i] else f"Column {i + 1}"
                    if first:
                        self.emit(f"* {label}: {c}".rstrip(": "))
                        first = False
                    else:
                        self.text_line(f"{label}: {c}".rstrip(": "), line_no, prefix="  ")
            self.blank()
        self.flush_links(links)

    def deflist(self, b: Block) -> None:
        line_no = b.line[0]
        self.warn.add(
            "DEFLIST", "info", line_no, "Definition lists are shown as bullets: Term — definition."
        )
        links: list[_Link] = []
        term = ""
        for child in b.children:
            if child.kind == "def_term":
                term = collapse_ws(self.inline(child.inlines, links, child.line[0]))
            elif child.kind == "def_desc":
                paras = [c for c in child.children if c.kind == "paragraph"]
                if len(paras) == 1 and len(child.children) == 1:
                    desc = collapse_ws(
                        self.inline(paras[0].inlines, links, paras[0].line[0]).replace("\n", " ")
                    )
                    self.emit(f"* {term} — {desc}")
                else:
                    self.text_line(term, child.line[0])
                    self.blocks(child.children, prefix="  ")
        self.blank()
        self.flush_links(links)

    def html_block(self, b: Block) -> None:
        line_no = b.line[0]
        raw = b.text
        if re.match(r"\s*<!--.*?-->\s*$", raw, re.S):
            return
        info = _HtmlInfo(raw)
        links: list[_Link] = []
        if info.embed_tag in ("script", "style") and not info.text_lines():
            self.warn.add(
                "HTML-STRIPPED",
                "info",
                line_no,
                f"<{info.embed_tag}> block has no place on Gemini and was dropped.",
            )
            return
        if info.embed_tag:
            self.warn.add(
                "HTML-EMBED",
                "warn",
                line_no,
                f"Embedded <{info.embed_tag}> is not available on Gemini; a notice and link were inserted.",
                [Alternative("Write a Gemini alternative in a :::only-gemini block")],
            )
            self.text_line(self.cfg.embed_notice, line_no)
            if info.embed_src:
                self.emit(f"=> {_encode_url(info.embed_src)} {display_url(info.embed_src)}")
            self.blank()
            return
        if info.img_src is not None:
            fake = Inline(
                "image",
                info.img_alt,
                ref=self._external_or_media(info.img_src),
                title=info.caption or None,
            )
            self.image_block(fake, line_no)
            return
        if info.details_summary is not None:
            self.warn.add(
                "HTML-DETAILS", "info", line_no, "Collapsible section is always expanded on Gemini."
            )
            self.text_line(info.details_summary, line_no)
        else:
            self.warn.add("HTML-STRIPPED", "info", line_no, "HTML block converted to plain text.")
        for url, label in info.links:
            links.append(_Link(url, label or display_url(url)))
        for part in info.text_lines():
            self.text_line(part, line_no)
        self.blank()
        self.flush_links(links)

    def _external_or_media(self, src: str) -> Ref:
        from constellation.core.routes import RefResolver

        return RefResolver().resolve(src, is_image=True)

    def footnotes(self) -> None:
        self.warn.add(
            "FOOTNOTE-END", "info", 0, "Footnotes are gathered under a Notes heading at the end."
        )
        self.blank()
        if self.cfg.notes_heading:
            self.emit(self.cfg.notes_heading)
        for key in self.doc.footnote_order:
            blocks = self.doc.footnotes[key]
            links: list[_Link] = []
            texts: list[str] = []
            for child in blocks:
                if child.kind == "paragraph":
                    texts.append(
                        collapse_ws(
                            self.inline(child.inlines, links, child.line[0]).replace("\n", " ")
                        )
                    )
                else:
                    texts.append(collapse_ws(inline_text(child.inlines)))
            self.text_line(
                f"[{key}] " + " ".join(t for t in texts if t), blocks[0].line[0] if blocks else 0
            )
            self.flush_links(links)
        self.blank()


def _is_image_paragraph(b: Block) -> bool:
    real = [i for i in b.inlines if not (i.kind in ("text", "soft_break") and not i.text.strip())]
    return len(real) == 1 and real[0].kind == "image"


def _is_link_paragraph(b: Block) -> bool:
    """A paragraph whose only content is one link (``[text](url)`` on a line of its own)."""
    real = [i for i in b.inlines if not (i.kind in ("text", "soft_break") and not i.text.strip())]
    return len(real) == 1 and real[0].kind == "link"


def _sentence_with(context: str, needle: str) -> str:
    """The sentence of ``context`` that contains ``needle`` (or the start of the context)."""
    sentences = re.split(r"(?<=[.!?])\s+", context)
    for s in sentences:
        if needle and needle in s:
            return s.strip()
    return sentences[0].strip() if sentences else context


def _encode_url(url: str) -> str:
    from urllib.parse import quote

    return quote(url, safe=":/?#[]@!$&'()*+,;=%-._~")


class _HtmlInfo(HTMLParser):
    """Extracts what the gemtext renderer needs from an HTML block."""

    def __init__(self, raw: str) -> None:
        super().__init__()
        self.embed_tag: str | None = None
        self.embed_src: str | None = None
        self.img_src: str | None = None
        self.img_alt = ""
        self.caption = ""
        self.details_summary: str | None = None
        self.links: list[tuple[str, str]] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._in_caption = False
        self._in_summary = False
        self._skip = 0
        self._parts: list[str] = []
        self.feed(raw)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in EMBED_TAGS:
            if self.embed_tag is None:
                self.embed_tag = tag
                self.embed_src = a.get("src") or a.get("href") or None
            self._skip += 1
        elif tag == "img" and self.img_src is None:
            self.img_src = a.get("src", "")
            self.img_alt = a.get("alt", "")
        elif tag == "figcaption":
            self._in_caption = True
        elif tag == "summary":
            self._in_summary = True
            self.details_summary = ""
        elif tag == "a":
            self._link_href = a.get("href")
            self._link_text = []
        elif tag == "br" or tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in EMBED_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "figcaption":
            self._in_caption = False
        elif tag == "summary":
            self._in_summary = False
        elif tag == "a" and self._link_href is not None:
            self.links.append((self._link_href, collapse_ws("".join(self._link_text))))
            self._link_href = None
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_caption:
            self.caption += data
        if self._in_summary and self.details_summary is not None:
            self.details_summary += data
            return
        if self._link_href is not None:
            self._link_text.append(data)
        self._parts.append(data)

    def text_lines(self) -> list[str]:
        text = "".join(self._parts)
        if self.details_summary is not None:
            text = text.replace(self.details_summary, "", 1)
        return [collapse_ws(p) for p in text.split("\n") if collapse_ws(p)]


def prefix_headings(text: str, prefix: str, levels: tuple[int, ...] = (2, 3)) -> str:
    """Put ``prefix`` in front of the text of gemtext headings at ``levels`` (a theme filter).

    Lines inside preformatted blocks are left alone, as are headings that already start with
    the prefix. ``## Posts`` with prefix ``"// "`` becomes ``## // Posts``.
    """
    if not prefix:
        return text
    out: list[str] = []
    pre = False
    for line in text.split("\n"):
        if line.startswith("```"):
            pre = not pre
        elif not pre:
            m = re.match(r"(#{1,3})[ \t]*(.*)$", line)
            if m and len(m.group(1)) in levels and not m.group(2).startswith(prefix.strip()):
                line = f"{m.group(1)} {prefix}{m.group(2)}"
        out.append(line)
    return "\n".join(out)
