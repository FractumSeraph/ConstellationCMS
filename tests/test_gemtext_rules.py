"""Targeted checks of gemtext rules that the golden files would not make obvious."""

from __future__ import annotations

from constellation.core.models import RenderGemini
from tests.helpers import gem

GEMTEXT_LINE = r"^(=> ?\S+.*|#{1,3} .*|\* .*|>.*|```.*|[^#*>=`].*|[=`].*|)$"


def codes(res) -> set[str]:
    return {w.code for w in res.warnings}


def test_links_follow_their_block_without_blank_line() -> None:
    out = gem("Text with [a link](https://a.example/x).\n\nNext para.\n").text
    assert out == "Text with a link.\n=> https://a.example/x a link\n\nNext para.\n"


def test_h4_becomes_text_line_then_blank() -> None:
    res = gem("#### Step one\n\nBody.\n")
    assert res.text == "Step one\n\nBody.\n"
    assert "HEADING-TOO-DEEP" in codes(res)


def test_h4_promote_option() -> None:
    res = gem("#### Step one\n", RenderGemini(heading_overflow="promote"))
    assert res.text == "### Step one\n"


def test_h1_in_body_is_demoted() -> None:
    res = gem("# Not the title\n")
    assert res.text == "## Not the title\n"
    assert "H1-IN-BODY" in codes(res)


def test_image_notice_and_link_with_owner_wording() -> None:
    res = gem("![A cat](cat.png)\n")
    assert res.text == "[Image, web only. Alt text: A cat]\n=> /media/posts/post/cat.png A cat\n"
    assert "IMG-NO-ALT" not in codes(res)


def test_missing_alt_is_a_warning_not_an_error() -> None:
    res = gem("![](cat.png)\n")
    w = next(w for w in res.warnings if w.code == "IMG-NO-ALT")
    assert w.severity == "warn"


def test_inline_markup_kept_by_default_and_strippable() -> None:
    assert gem("some *em* and **strong** and `code`\n").text == "some *em* and **strong** and `code`\n"
    assert gem("some *em* and **strong** and `code`\n", RenderGemini(inline_markup="strip")).text == "some em and strong and code\n"


def test_reserved_prefix_is_defused() -> None:
    res = gem("#hashtag starts this paragraph\n")
    assert res.text == " #hashtag starts this paragraph\n"
    assert "PREFIX-ESCAPED" in codes(res)


def test_hard_breaks_inside_quote_become_quote_lines() -> None:
    res = gem("> one<br>\n> two  \n> three\n")
    assert res.text == "> one\n> two\n> three\n"


def test_quote_paragraphs_separated_by_bare_marker() -> None:
    assert gem("> a\n>\n> b\n").text == "> a\n>\n> b\n"


def test_ordered_list_numbers_are_text_lines() -> None:
    res = gem("3. three\n4. four\n")
    assert res.text == "3. three\n4. four\n"
    assert "LIST-ORDERED" in codes(res)


def test_list_of_links_becomes_link_lines() -> None:
    assert gem("- [Alpha](post:alpha)\n- [Foo](post:foo)\n").text == "=> /posts/alpha.gmi Alpha\n=> /posts/foo.gmi Foo\n"


def test_nested_list_indent_default_and_flatten() -> None:
    src = "- a\n  - b\n"
    assert gem(src).text == "* a\n  * b\n"
    assert gem(src, RenderGemini(nested_lists="flatten")).text == "* a\n* → b\n"


def test_long_cell_table_becomes_records() -> None:
    src = "| Rank | When |\n|---|---|\n| I | You notice the brokenness and decide to act, however small. |\n"
    res = gem(src)
    assert res.text == "* I — You notice the brokenness and decide to act, however small.\n"
    assert "TABLE-RECORDS" in codes(res)


def test_short_table_is_preformatted() -> None:
    res = gem("| A | B |\n|---|---|\n| 1 | 2 |\n")
    assert res.text == "```table: A, B\nA  B\n-  -\n1  2\n```\n"


def test_hr_uses_four_tildes() -> None:
    assert gem("a\n\n---\n\nb\n").text == "a\n\n~~~~\n\nb\n"


def test_fence_inside_code_is_escaped_and_errors() -> None:
    res = gem("```text\nhello\n```inner\n```\n")
    assert res.text == "```text\nhello\n ```inner\n```\n"
    assert any(w.code == "PRE-FENCE-INSIDE" and w.severity == "error" for w in res.warnings)


def test_embed_is_replaced_by_notice_and_link() -> None:
    res = gem('<iframe src="https://www.youtube.com/embed/x"></iframe>\n')
    assert res.text == "[Embedded content, web only]\n=> https://www.youtube.com/embed/x www.youtube.com/embed/x\n"
    assert "HTML-EMBED" in codes(res)


def test_only_gemini_container_is_verbatim_and_only_web_is_dropped() -> None:
    src = ":::only-gemini\n=> gemini://x Capsule link\n:::\n\n:::only-web\n<p>web</p>\n:::\n"
    assert gem(src).text == "=> gemini://x Capsule link\n"


def test_footnotes_gather_under_notes() -> None:
    res = gem("Text[^1] here.\n\n[^1]: The note.\n")
    assert res.text == "Text[1] here.\n\n### Notes\n[1] The note.\n"


def test_generic_link_text_gets_context_label() -> None:
    res = gem("Buy the bolts. See [here](https://a.example) for the list. Then go.\n")
    assert "=> https://a.example here: See here for the list." in res.text
    assert "LINK-GENERIC-TEXT" in codes(res)


def test_link_markers_option() -> None:
    res = gem("See [the docs](https://d.example).\n", RenderGemini(link_markers="numbers"))
    assert res.text == "See the docs[1].\n=> https://d.example [1] the docs\n"


def test_every_line_is_valid_gemtext() -> None:
    import re

    src = "# T\n\ntext\n\n- a\n- b\n\n> q\n\n```\ncode\n```\n\n[l](https://x.example)\n"
    for line in gem(src).text.split("\n"):
        assert re.match(GEMTEXT_LINE, line), line
