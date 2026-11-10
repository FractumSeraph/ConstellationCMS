"""Document → plain text: summaries, meta descriptions and, later, Gopher files."""

from __future__ import annotations

import textwrap

from constellation.core.markdown.document import Block, Document, Inline


def plain_text(doc: Document, *, width: int | None = None, max_chars: int | None = None) -> str:
    """Readable plain text. Links keep their text; images become their alt text."""
    paras: list[str] = []
    for b in doc.blocks:
        paras.extend(_block(b))
    out: list[str] = []
    for p in paras:
        if not p:
            continue
        out.append(textwrap.fill(p, width) if width else p)
    text = "\n\n".join(out)
    if max_chars is not None and len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:")
        text = cut + "…"
    return text


def summary_text(doc: Document, max_chars: int = 240, words: int = 0) -> str:
    """The first paragraph of prose, for lists, feeds and meta descriptions.

    With ``words``, the first that many words instead, read across paragraphs and ending in "…"
    when the text goes on.
    """
    if words > 0:
        taken: list[str] = []
        more = False
        for b in doc.blocks:
            if b.kind != "paragraph":
                continue
            text = _inline(b.inlines).strip()
            if not text or text.startswith("[image"):
                continue
            for word in text.split():
                if len(taken) == words:
                    more = True
                    break
                taken.append(word)
            if more:
                break
        out = " ".join(taken)
        return out.rstrip(",;:") + "…" if more else out
    for b in doc.blocks:
        if b.kind == "paragraph":
            text = _inline(b.inlines).strip()
            if text and not text.startswith("[image"):
                if len(text) > max_chars:
                    text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
                return text
    return ""


def _block(b: Block) -> list[str]:
    k = b.kind
    if k == "paragraph":
        return [_inline(b.inlines)]
    if k == "heading":
        return [_inline(b.inlines)]
    if k == "list":
        out: list[str] = []
        n = b.start
        for item in b.children:
            lead = f"{n}. " if b.ordered else "- "
            texts = [t for c in item.children for t in _block(c)]
            if texts:
                out.append(lead + texts[0])
                out.extend("  " + t for t in texts[1:])
            n += 1
        return ["\n".join(out)] if out else []
    if k == "quote":
        return ["\n".join("> " + t for c in b.children for t in _block(c))]
    if k == "code":
        return [b.text.rstrip("\n")]
    if k == "table":
        rows = [" | ".join(_inline(c) for c in r) for r in b.rows]
        return ["\n".join(rows)]
    if k == "hr":
        return ["* * *"]
    if k == "html":
        from constellation.core.render.base import collapse_ws, strip_tags

        return [collapse_ws(strip_tags(b.text))]
    if k == "gallery":
        return []
    if k == "container":
        return [t for c in b.children for t in _block(c)] if b.info == "web" else []
    if k == "deflist":
        return [
            t
            for c in b.children
            for t in (_block(c) if c.kind == "def_desc" else [_inline(c.inlines)])
        ]
    return [_inline(b.inlines)]


def _inline(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for n in inlines:
        if n.kind in ("text", "code"):
            parts.append(n.text)
        elif n.kind == "soft_break":
            parts.append(" ")
        elif n.kind == "hard_break":
            parts.append("\n")
        elif n.kind == "image":
            parts.append(f"[image: {n.text}]" if n.text else "[image]")
        elif n.kind == "footnote_ref":
            parts.append(f"[{n.text}]")
        elif n.kind == "html":
            from constellation.core.render.base import strip_tags

            parts.append(strip_tags(n.text))
        else:
            parts.append(_inline(n.children))
    return "".join(parts)
