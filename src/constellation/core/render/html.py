"""Document → HTML fragment. Theme templates wrap the fragment into a page."""

from __future__ import annotations

from html import escape

from slugify import slugify

from constellation.core.markdown.document import (
    Block,
    Document,
    Inline,
    gallery_extras,
    gallery_images,
    inline_text,
)
from constellation.core.render.base import RenderResult, UrlFor, WarningSink


class HtmlRenderer:
    platform = "web"

    def __init__(self, url_for: UrlFor, *, image_srcset: dict[str, str] | None = None) -> None:
        self.url_for = url_for
        self.image_srcset = image_srcset or {}
        self.headings: list[tuple[int, str, str]] = []

    def render(self, doc: Document) -> RenderResult:
        st = _State(self, doc)
        out = st.blocks(doc.blocks)
        if doc.footnote_order:
            out += st.footnotes()
        # Headings in order, for a table of contents: (level as shown, text, id).
        self.headings = st.headings
        return RenderResult(out, st.warn.items)


class _State:
    def __init__(self, r: HtmlRenderer, doc: Document) -> None:
        self.r = r
        self.doc = doc
        self.warn = WarningSink("web")
        self.heading_ids: set[str] = set()
        self.headings: list[tuple[int, str, str]] = []
        self.galleries = 0

    def blocks(self, blocks: list[Block]) -> str:
        return "".join(self.block(b) for b in blocks)

    def block(self, b: Block) -> str:
        k = b.kind
        if k == "paragraph":
            if _is_image_paragraph(b):
                return self.figure(b.inlines[0])
            return f"<p>{self.inlines(b.inlines)}</p>\n"
        if k == "heading":
            level = b.level
            if level == 1:
                self.warn.add(
                    "H1-IN-BODY",
                    "warn",
                    b.line[0],
                    "Body contains a level-1 heading; the post title is already the H1. It will be shown as a level-2 heading.",
                )
                level = 2
            text = self.inlines(b.inlines)
            plain = inline_text(b.inlines)
            hid = self._heading_id(plain)
            self.headings.append((level, plain.strip(), hid))
            return f'<h{level} id="{hid}">{text}</h{level}>\n'
        if k == "list":
            tag = "ol" if b.ordered else "ul"
            start = f' start="{b.start}"' if b.ordered and b.start != 1 else ""
            items = "".join(self.list_item(i) for i in b.children)
            return f"<{tag}{start}>\n{items}</{tag}>\n"
        if k == "quote":
            return f"<blockquote>\n{self.blocks(b.children)}</blockquote>\n"
        if k == "code":
            lang = b.info.split()[0] if b.info else ""
            cls = f' class="language-{escape(lang)}"' if lang else ""
            return f"<pre><code{cls}>{escape(b.text)}</code></pre>\n"
        if k == "table":
            return self.table(b)
        if k == "hr":
            return "<hr>\n"
        if k == "html":
            return b.text if b.text.endswith("\n") else b.text + "\n"
        if k == "container":
            return self.blocks(b.children) if b.info == "web" else ""
        if k == "gallery":
            return self.gallery(b)
        if k == "deflist":
            parts = ["<dl>\n"]
            for c in b.children:
                if c.kind == "def_term":
                    parts.append(f"<dt>{self.inlines(c.inlines)}</dt>\n")
                else:
                    parts.append(f"<dd>{self.blocks(c.children)}</dd>\n")
            parts.append("</dl>\n")
            return "".join(parts)
        return f"<p>{self.inlines(b.inlines)}</p>\n"

    def list_item(self, item: Block) -> str:
        inner = self.blocks(item.children)
        tight = len(item.children) == 1 and item.children[0].kind == "paragraph"
        if tight:
            inner = inner.strip()
            if inner.startswith("<p>") and inner.endswith("</p>"):
                inner = inner[3:-4]
        if item.task:
            checked = " checked" if item.task == "done" else ""
            return f'<li class="task-list-item"><input type="checkbox" disabled{checked}> {inner}</li>\n'
        return f"<li>{inner}</li>\n"

    def table(self, b: Block) -> str:
        if not b.rows:
            return ""
        out = ["<table>\n<thead>\n<tr>"]
        for i, cell in enumerate(b.rows[0]):
            out.append(f"<th{self._align(b, i)}>{self.inlines(cell)}</th>")
        out.append("</tr>\n</thead>\n")
        if len(b.rows) > 1:
            out.append("<tbody>\n")
            for row in b.rows[1:]:
                out.append("<tr>")
                for i, cell in enumerate(row):
                    out.append(f"<td{self._align(b, i)}>{self.inlines(cell)}</td>")
                out.append("</tr>\n")
            out.append("</tbody>\n")
        out.append("</table>\n")
        return "".join(out)

    def _align(self, b: Block, i: int) -> str:
        a = b.aligns[i] if i < len(b.aligns) else ""
        return f' style="text-align:{a}"' if a else ""

    def figure(self, img: Inline) -> str:
        assert img.ref is not None
        src = self.r.url_for(img.ref)
        alt = escape(img.text, quote=True)
        srcset = self.r.image_srcset.get(src)
        extra = (
            f' srcset="{escape(srcset, quote=True)}" sizes="(max-width: 48rem) 100vw, 48rem"'
            if srcset
            else ""
        )
        cap = f"<figcaption>{escape(img.title)}</figcaption>" if img.title else ""
        if not img.text.strip():
            self.warn.add("IMG-NO-ALT", "warn", 0, "Image has no alt text.")
        return f'<figure><img src="{escape(src, quote=True)}" alt="{alt}" loading="lazy"{extra}>{cap}</figure>\n'

    def gallery(self, b: Block) -> str:
        """A grid of images; each links to the full-size file, which the lightbox opens."""
        images = gallery_images(b)
        if gallery_extras(b):
            self.warn.add(
                "GALLERY-NOT-IMAGE",
                "warn",
                b.line[0],
                "A gallery holds something other than images; only the images are shown.",
            )
        if not images:
            return ""
        self.galleries += 1
        group = f"gallery-{self.galleries}"
        cols = f' style="--gallery-cols:{int(b.info)}"' if b.info.isdigit() else ""
        parts = [f'<div class="gallery"{cols}>\n']
        for img in images:
            assert img.ref is not None
            src = self.r.url_for(img.ref)
            srcset = self.r.image_srcset.get(src)
            extra = (
                f' srcset="{escape(srcset, quote=True)}" sizes="(max-width: 48rem) 50vw, 20rem"'
                if srcset
                else ""
            )
            if not img.text.strip():
                self.warn.add("IMG-NO-ALT", "warn", b.line[0], "Image has no alt text.")
            cap = escape(img.title) if img.title else ""
            title = f' data-caption="{escape(img.title, quote=True)}"' if img.title else ""
            parts.append(
                f'<figure class="gallery-item"><a href="{escape(src, quote=True)}" '
                f'data-lightbox="{group}"{title}><img src="{escape(src, quote=True)}" '
                f'alt="{escape(img.text, quote=True)}" loading="lazy"{extra}></a>'
                + (f"<figcaption>{cap}</figcaption>" if cap else "")
                + "</figure>\n"
            )
        parts.append("</div>\n")
        return "".join(parts)

    def inlines(self, inlines: list[Inline]) -> str:
        out: list[str] = []
        for n in inlines:
            k = n.kind
            if k == "text":
                out.append(escape(n.text, quote=False))
            elif k == "soft_break":
                out.append("\n")
            elif k == "hard_break":
                out.append("<br>")
            elif k == "emphasis":
                out.append(f"<em>{self.inlines(n.children)}</em>")
            elif k == "strong":
                out.append(f"<strong>{self.inlines(n.children)}</strong>")
            elif k == "strike":
                out.append(f"<del>{self.inlines(n.children)}</del>")
            elif k == "code":
                out.append(f"<code>{escape(n.text, quote=False)}</code>")
            elif k == "link":
                assert n.ref is not None
                href = escape(self.r.url_for(n.ref), quote=True)
                title = f' title="{escape(n.title, quote=True)}"' if n.title else ""
                rel = ' rel="noopener"' if n.ref.kind == "external" else ""
                out.append(f'<a href="{href}"{title}{rel}>{self.inlines(n.children)}</a>')
            elif k == "image":
                assert n.ref is not None
                src = escape(self.r.url_for(n.ref), quote=True)
                title = f' title="{escape(n.title, quote=True)}"' if n.title else ""
                out.append(
                    f'<img src="{src}" alt="{escape(n.text, quote=True)}"{title} loading="lazy">'
                )
            elif k == "footnote_ref":
                out.append(
                    f'<sup class="footnote-ref"><a href="#fn-{n.text}" id="fnref-{n.text}">{n.text}</a></sup>'
                )
            elif k == "html":
                out.append(n.text)
        return "".join(out)

    def footnotes(self) -> str:
        parts = ['<section class="footnotes">\n<ol>\n']
        for key in self.doc.footnote_order:
            body = self.blocks(self.doc.footnotes[key]).strip()
            back = f' <a href="#fnref-{key}" class="footnote-backref">↩</a>'
            if body.endswith("</p>"):
                body = body[:-4] + back + "</p>"
            else:
                body += back
            parts.append(f'<li id="fn-{key}">{body}</li>\n')
        parts.append("</ol>\n</section>\n")
        return "".join(parts)

    def _heading_id(self, text: str) -> str:
        base = slugify(text) or "section"
        hid, n = base, 2
        while hid in self.heading_ids:
            hid, n = f"{base}-{n}", n + 1
        self.heading_ids.add(hid)
        return hid


def _is_image_paragraph(b: Block) -> bool:
    real = [i for i in b.inlines if not (i.kind in ("text", "soft_break") and not i.text.strip())]
    return len(real) == 1 and real[0].kind == "image"
