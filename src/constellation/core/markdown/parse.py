"""Markdown → :class:`Document` using markdown-it-py's token stream.

The parser is deliberately strict about what it recognises so that every renderer sees the same
tree. Every block keeps its source line range from the token ``map``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdown_it.tree import SyntaxTreeNode
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin

from constellation.core.markdown.document import Block, Document, Inline, Ref, inline_text

CONTAINERS = ("only-web", "only-gemini", "only-gopher")
Resolver = Callable[[str, bool], Ref]
WIKILINK = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
TASK_RX = re.compile(r"^\[( |x|X)\]\s+")


def make_parser() -> MarkdownIt:
    md = MarkdownIt("gfm-like").use(footnote_plugin).use(deflist_plugin)
    for name in CONTAINERS:
        md.use(container_plugin, name)
    md.enable("table")
    return md


_MD: MarkdownIt | None = None


def parser() -> MarkdownIt:
    global _MD
    if _MD is None:
        _MD = make_parser()
    return _MD


def default_resolver(href: str, is_image: bool) -> Ref:
    from constellation.core.routes import RefResolver

    return RefResolver().resolve(href, is_image=is_image)


def parse_markdown(source: str, *, resolve: Resolver = default_resolver) -> Document:
    source = _expand_wikilinks(source)
    tokens = parser().parse(source)
    lines = source.split("\n")
    tree = SyntaxTreeNode(tokens)
    builder = _Builder(resolve, lines)
    doc = Document()
    for node in tree.children:
        if node.type == "footnote_block":
            builder.footnotes(node, doc)
            continue
        doc.blocks.extend(builder.block(node))
    doc.refs = builder.refs
    doc.footnote_order = sorted(doc.footnotes, key=lambda k: int(k))
    return doc


def _expand_wikilinks(source: str) -> str:
    """``[[slug]]`` and ``[[slug|text]]`` become ``[text](post:slug)``, outside code fences."""
    out: list[str] = []
    in_fence = False
    for line in source.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence:
            line = WIKILINK.sub(lambda m: f"[{m.group(2) or m.group(1)}](post:{m.group(1).strip()})", line)
        out.append(line)
    return "\n".join(out)


class _Builder:
    def __init__(self, resolve: Resolver, lines: list[str]) -> None:
        self.resolve = resolve
        self.lines = lines
        self.refs: list[Ref] = []

    # ---- blocks -------------------------------------------------------------------------
    def block(self, node: SyntaxTreeNode) -> list[Block]:
        t = node.type
        line = _line(node)
        if t == "paragraph":
            return [Block("paragraph", line, inlines=self.inlines_of(node))]
        if t == "heading":
            return [Block("heading", line, level=int(node.tag[1]), inlines=self.inlines_of(node))]
        if t in ("bullet_list", "ordered_list"):
            start = int(node.attrs.get("start", 1)) if t == "ordered_list" else 1
            items = [self.list_item(child) for child in node.children]
            return [Block("list", line, ordered=t == "ordered_list", start=start, children=items)]
        if t == "blockquote":
            return [Block("quote", line, children=self.blocks(node.children))]
        if t == "fence":
            return [Block("code", line, info=(node.info or "").strip(), text=node.content)]
        if t == "code_block":
            return [Block("code", line, info="", text=node.content)]
        if t == "hr":
            return [Block("hr", line)]
        if t == "html_block":
            return [Block("html", line, text=node.content)]
        if t == "table":
            return [self.table(node)]
        if t == "dl":
            return [self.deflist(node)]
        if t.startswith("container_"):
            platform = t[len("container_only-"):]
            raw = "\n".join(self.lines[line[0]: line[1]]) if line[1] > line[0] else ""
            return [Block("container", line, info=platform, text=raw, children=self.blocks(node.children))]
        if t == "footnote_anchor":
            return []
        # Unknown node: keep its text so nothing silently disappears.
        if node.children:
            return self.blocks(node.children)
        return [Block("paragraph", line, inlines=[Inline("text", node.content or "")])]

    def blocks(self, nodes: list[SyntaxTreeNode]) -> list[Block]:
        out: list[Block] = []
        for n in nodes:
            out.extend(self.block(n))
        return out

    def list_item(self, node: SyntaxTreeNode) -> Block:
        children = self.blocks(node.children)
        item = Block("list_item", _line(node), children=children)
        if children and children[0].kind == "paragraph" and children[0].inlines:
            first = children[0].inlines[0]
            if first.kind == "text":
                m = TASK_RX.match(first.text)
                if m:
                    item.task = "done" if m.group(1).lower() == "x" else "todo"
                    first.text = first.text[m.end():]
        return item

    def table(self, node: SyntaxTreeNode) -> Block:
        rows: list[list[list[Inline]]] = []
        aligns: list[str] = []
        for section in node.children:
            for tr in section.children:
                row: list[list[Inline]] = []
                for cell in tr.children:
                    row.append(self.inlines_of(cell))
                    if section.type == "thead":
                        style = str(cell.attrs.get("style", ""))
                        aligns.append(style.split(":")[-1].strip() if "text-align" in style else "")
                rows.append(row)
        return Block("table", _line(node), rows=rows, aligns=aligns)

    def deflist(self, node: SyntaxTreeNode) -> Block:
        children: list[Block] = []
        for child in node.children:
            if child.type == "dt":
                children.append(Block("def_term", _line(child), inlines=self.inlines(child.children)))
            elif child.type == "dd":
                children.append(Block("def_desc", _line(child), children=self.blocks(child.children)))
        return Block("deflist", _line(node), children=children)

    def footnotes(self, node: SyntaxTreeNode, doc: Document) -> None:
        for fn in node.children:
            if fn.type != "footnote":
                continue
            fid = int(fn.meta.get("id", 0)) + 1
            doc.footnotes[str(fid)] = self.blocks([c for c in fn.children if c.type != "footnote_anchor"])

    # ---- inlines ------------------------------------------------------------------------
    def inlines_of(self, node: SyntaxTreeNode) -> list[Inline]:
        """Inlines of a block node whose single child is the ``inline`` node."""
        for child in node.children:
            if child.type == "inline":
                return self.inlines(child.children)
        if node.type == "inline":
            return self.inlines(node.children)
        return []

    def inlines(self, nodes: list[SyntaxTreeNode]) -> list[Inline]:
        out: list[Inline] = []
        for n in nodes:
            t = n.type
            if t == "text":
                out.append(Inline("text", n.content))
            elif t == "softbreak":
                out.append(Inline("soft_break"))
            elif t == "hardbreak":
                out.append(Inline("hard_break"))
            elif t == "em":
                out.append(Inline("emphasis", children=self.inlines(n.children)))
            elif t == "strong":
                out.append(Inline("strong", children=self.inlines(n.children)))
            elif t == "s":
                out.append(Inline("strike", children=self.inlines(n.children)))
            elif t == "code_inline":
                out.append(Inline("code", n.content))
            elif t == "link":
                ref = self.resolve(str(n.attrs.get("href", "")), False)
                self.refs.append(ref)
                title = n.attrs.get("title")
                out.append(Inline("link", ref=ref, title=str(title) if title else None,
                                  children=self.inlines(n.children)))
            elif t == "image":
                ref = self.resolve(str(n.attrs.get("src", "")), True)
                self.refs.append(ref)
                title = n.attrs.get("title")
                alt = inline_text(self.inlines(n.children)).strip()
                out.append(Inline("image", alt, ref=ref, title=str(title) if title else None))
            elif t == "html_inline":
                content = n.content or ""
                if re.fullmatch(r"<br\s*/?>", content.strip(), re.I):
                    out.append(Inline("hard_break"))
                else:
                    out.append(Inline("html", content))
            elif t == "footnote_ref":
                out.append(Inline("footnote_ref", str(int(n.meta.get("id", 0)) + 1)))
            elif n.children:
                out.extend(self.inlines(n.children))
            elif n.content:
                out.append(Inline("text", n.content))
        return out


def _line(node: SyntaxTreeNode) -> tuple[int, int]:
    m = node.map
    if m is None:
        # Inline-level or synthetic nodes: inherit from the nearest ancestor with a map.
        parent = node.parent
        while parent is not None and parent.map is None:
            parent = parent.parent
        m = parent.map if parent is not None else None
    if m is None:
        return (0, 0)
    return (m[0] + 1, m[1])


def tokens_for_debug(source: str) -> list[Token]:
    return parser().parse(source)
