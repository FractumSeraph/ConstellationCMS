"""Property tests: whatever Markdown comes in, the gemtext that comes out is well formed."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.helpers import gem

WORD = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Zs"), blacklist_characters="\r\x0b\x0c  "
    ),
    min_size=1,
    max_size=12,
)
WORDS = st.lists(WORD, min_size=1, max_size=8).map(" ".join)
INLINE = st.one_of(
    WORDS,
    WORDS.map(lambda w: f"*{w}*"),
    WORDS.map(lambda w: f"**{w}**"),
    WORDS.map(lambda w: f"`{w}`"),
    WORDS.map(lambda w: f"[{w}](https://e.example/{len(w)})"),
    WORDS.map(lambda w: f"![{w}](pic.png)"),
    WORDS.map(lambda w: f"~~{w}~~"),
)
PARAGRAPH = st.lists(INLINE, min_size=1, max_size=5).map(" ".join)
HEADING = st.tuples(st.integers(1, 6), WORDS).map(lambda t: "#" * t[0] + " " + t[1])
LIST = st.lists(WORDS, min_size=1, max_size=4).map(lambda xs: "\n".join(f"- {x}" for x in xs))
OLIST = st.lists(WORDS, min_size=1, max_size=4).map(
    lambda xs: "\n".join(f"{i + 1}. {x}" for i, x in enumerate(xs))
)
QUOTE = st.lists(WORDS, min_size=1, max_size=3).map(lambda xs: "\n".join(f"> {x}" for x in xs))
FENCE = st.lists(WORDS, min_size=0, max_size=3).map(lambda xs: "```\n" + "\n".join(xs) + "\n```")
TABLE = st.lists(st.tuples(WORDS, WORDS), min_size=1, max_size=3).map(
    lambda rows: "| A | B |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)
)
BLOCK = st.one_of(PARAGRAPH, HEADING, LIST, OLIST, QUOTE, FENCE, TABLE, st.just("---"))
DOCUMENT = st.lists(BLOCK, min_size=1, max_size=8).map(lambda bs: "\n\n".join(bs) + "\n")


@settings(max_examples=150, deadline=None)
@given(DOCUMENT)
def test_gemtext_is_well_formed(src: str) -> None:
    out = gem(src).text
    assert "\r" not in out
    assert "﻿" not in out
    assert out == "" or out.endswith("\n")
    in_pre = False
    prev_blank = False
    for line in out.split("\n")[:-1]:
        if line.startswith("```"):
            in_pre = not in_pre
            prev_blank = False
            continue
        if in_pre:
            assert not line.startswith("```")
            continue
        assert not line.startswith("####"), line
        if line.startswith("=>"):
            parts = line[2:].strip().split(" ", 1)
            assert parts and parts[0], line
        assert line == line.rstrip(), repr(line)
        assert not (prev_blank and line == ""), "double blank line"
        prev_blank = line == ""
    assert not in_pre, "unbalanced preformatted block"


@settings(max_examples=60, deadline=None)
@given(DOCUMENT)
def test_rendering_is_deterministic(src: str) -> None:
    assert gem(src).text == gem(src).text
