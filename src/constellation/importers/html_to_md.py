"""Turn the HTML of a post body into Markdown, for importers.

It covers what blog editors produce: paragraphs, headings, emphasis, links, images, lists (nested,
ordered), block quotes, code, rules, line breaks, figures with captions, and simple tables. Anything
else (video, iframes, forms, complex tables) is kept as an HTML block, which the web output shows
as it was and the Gemini and Gopher renderers report.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from html.parser import HTMLParser

_BLOCK_RAW = {"iframe", "video", "audio", "object", "embed", "form", "svg", "canvas", "details"}
_SKIP = {"script", "style", "noscript", "template", "button"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "source", "track", "wbr", "area", "col"}


class _Raw:
    """Collects an element verbatim (for the HTML kept as a block)."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.depth = 0
        self.parts: list[str] = []


class _Converter(HTMLParser):
    def __init__(self, url: Callable[[str, bool], str]) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.out: list[str] = []
        self.links: list[str] = []
        self.lists: list[list[object]] = []  # [kind, counter]
        self.quote = 0
        self.pre = False
        self.skip = 0
        self.raw: _Raw | None = None
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None
        self.header_rows = 0

    # -- output helpers -------------------------------------------------------------------------
    def w(self, text: str) -> None:
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def block(self) -> None:
        # Inside a quote the blank line between paragraphs must keep its ">" or the quote ends.
        sep = "\n" + ">" * self.quote + "\n" if self.quote else "\n\n"
        target = self.cell if self.cell is not None else self.out
        tail = "".join(target[-3:])
        if tail.endswith(sep) or (not self.quote and tail.endswith("\n\n")):
            return
        self.w(sep)

    def close_mark(self, mark: str) -> None:
        # Markdown needs the closing marker right after a word: move trailing spaces outside.
        target = self.cell if self.cell is not None else self.out
        trail = ""
        while target and target[-1].endswith(" "):
            stripped = target[-1].rstrip(" ")
            trail += target[-1][len(stripped) :]
            target[-1] = stripped
            if stripped:
                break
            target.pop()
        if target and target[-1].endswith(("*", "_")) and target[-1].rstrip("*_") == "":
            target.pop()  # an empty emphasis: drop both markers
            self.w(trail)
            return
        self.w(mark + trail)

    # -- parser callbacks ------------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if self.raw is not None:
            if tag == self.raw.tag:
                self.raw.depth += 1
            self.raw.parts.append(self.get_starttag_text() or "")
            return
        if self.skip or tag in _SKIP:
            if tag not in _VOID:
                self.skip += 1
            return
        if tag in _BLOCK_RAW:
            self.raw = _Raw(tag)
            self.raw.parts.append(self.get_starttag_text() or "")
            return
        if tag == "table":
            self.table, self.header_rows = [], 0
            return
        if self.table is not None:
            if tag == "tr":
                self.table.append([])
            elif tag in ("td", "th"):
                self.cell = []
                if tag == "th" and len(self.table) == 1:
                    self.header_rows = 1
            elif tag == "br":
                self.w(" ")
            elif tag in ("strong", "b"):
                self.w("**")
            elif tag in ("em", "i"):
                self.w("*")
            elif tag == "a":
                self.links.append(a.get("href", ""))
                self.w("[")
            return
        if tag in ("p", "div", "section", "article", "header", "footer", "main", "aside", "figure"):
            self.block()
        elif re.fullmatch(r"h[1-6]", tag):
            self.block()
            self.w("#" * int(tag[1]) + " ")
        elif tag in ("strong", "b"):
            self.w("**")
        elif tag in ("em", "i", "cite"):
            self.w("*")
        elif tag in ("del", "s", "strike"):
            self.w("~~")
        elif tag == "a":
            self.links.append(a.get("href", ""))
            self.w("[")
        elif tag == "br":
            self.w("<br>\n" + "> " * self.quote)
        elif tag == "hr":
            self.w("\n\n---\n\n")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, int(a.get("start") or 1) - 1])
            if len(self.lists) == 1:
                self.block()
        elif tag == "li":
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] = int(str(kind[1])) + 1
            indent = "   " * (len(self.lists) - 1)
            marker = "- " if kind[0] == "ul" else f"{kind[1]}. "
            self.w("\n" + "> " * self.quote + indent + marker)
        elif tag == "blockquote":
            self.block()
            self.quote += 1
        elif tag == "img":
            src = self.url(a.get("src", ""), True)
            alt = a.get("alt", "").replace("]", "")
            title = a.get("title", "")
            self.w(f"![{alt}]({src}{f' {chr(34)}{title}{chr(34)}' if title else ''})")
        elif tag == "pre":
            self.pre = True
            lang = ""
            self.block()
            self.w("```" + lang + "\n")
        elif tag == "code":
            if self.pre:
                m = re.search(r"language-([\w+-]+)", a.get("class", ""))
                if m and self.out and self.out[-1] == "```\n":
                    self.out[-1] = f"```{m.group(1)}\n"
            else:
                self.w("`")
        elif tag == "figcaption":
            self.block()
            self.w("*")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.raw is not None:
            self.raw.parts.append(f"</{tag}>")
            if tag == self.raw.tag:
                if self.raw.depth == 0:
                    self.block()
                    self.w("".join(self.raw.parts))
                    self.block()
                    self.raw = None
                else:
                    self.raw.depth -= 1
            return
        if self.skip:
            if tag not in _VOID:
                self.skip -= 1
            return
        if self.table is not None:
            if tag in ("td", "th") and self.cell is not None:
                text = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|")
                if self.table:
                    self.table[-1].append(text)
                self.cell = None
            elif tag in ("strong", "b"):
                self.close_mark("**")
            elif tag in ("em", "i"):
                self.close_mark("*")
            elif tag == "a":
                href = self.links.pop() if self.links else ""
                self.w(f"]({self.url(href, False)})")
            elif tag == "table":
                rows, self.table = self.table, None
                self.write_table(rows)
            return
        if tag in ("strong", "b"):
            self.close_mark("**")
        elif tag in ("em", "i", "cite"):
            self.close_mark("*")
        elif tag in ("del", "s", "strike"):
            self.close_mark("~~")
        elif tag == "a":
            href = self.links.pop() if self.links else ""
            self.w(f"]({self.url(href, False)})")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self.block()
        elif tag == "blockquote":
            self.quote = max(0, self.quote - 1)
            self.block()
        elif tag == "pre":
            self.pre = False
            self.w("```" if "".join(self.out[-2:]).endswith("\n") else "\n```")
            self.block()
        elif tag == "code" and not self.pre:
            self.w("`")
        elif tag == "figcaption":
            self.close_mark("*")
            self.block()
        elif tag in ("p", "div", "section", "article", "figure") or re.fullmatch(r"h[1-6]", tag):
            self.block()

    def handle_data(self, data: str) -> None:
        if self.raw is not None:
            self.raw.parts.append(html.escape(data, quote=False))
            return
        if self.skip:
            return
        if self.pre:
            self.w(data)
            return
        text = re.sub(r"\s+", " ", data)
        if self.cell is None:
            text = _escape_markdown(text)
        if self.quote and self.out and self.out[-1].endswith("\n"):
            text = text.lstrip()
            if text:
                self.w("> " * self.quote)
        self.w(text)

    def write_table(self, rows: list[list[str]]) -> None:
        rows = [r for r in rows if r]
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        self.block()
        self.w("| " + " | ".join(rows[0]) + " |\n")
        self.w("|" + "---|" * width + "\n")
        for r in rows[1:]:
            self.w("| " + " | ".join(r) + " |\n")
        self.block()

    def markdown(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"(\n>+)(?:\n>+)+(?=\n)", r"\1", text)  # runs of empty quote lines
        text = re.sub(r"(^|\n\n)>+\n", r"\1", text)  # an empty quote line opening a quote
        text = re.sub(r"\n>+\n(?=\n|$)", "\n", text)  # ...or closing one
        text = re.sub(r"\n{3,}", "\n\n", text)
        return "\n".join(ln.rstrip() for ln in text.split("\n")).strip() + "\n"


def _escape_markdown(text: str) -> str:
    """Escape what would otherwise turn plain text into Markdown syntax."""
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([*_`\[\]<])", r"\\\1", text)
    return text


def html_to_markdown(fragment: str, url: Callable[[str, bool], str] | None = None) -> str:
    """Convert an HTML fragment to Markdown. ``url(href, is_image)`` rewrites link targets."""
    conv = _Converter(url or (lambda href, _img: href))
    conv.feed(fragment)
    conv.close()
    return conv.markdown()
