"""Every Markdown construct in the mapping table has a corpus file; its gemtext, HTML and
warnings are checked in. Run ``pytest --update-goldens`` after an intentional change and
review the diff: it reads as "this Markdown now renders as that gemtext"."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import gem, html

CORPUS = Path(__file__).parent / "corpus"
CASES = sorted(p.stem for p in CORPUS.glob("*.md"))


def _warnings_text(ws: list) -> str:
    return "".join(f"{w.line}:{w.code}:{w.severity}\n" for w in ws)


@pytest.mark.parametrize("name", CASES)
def test_golden(name: str, request: pytest.FixtureRequest) -> None:
    src = (CORPUS / f"{name}.md").read_text(encoding="utf-8")
    g = gem(src)
    h = html(src)
    outputs = {
        ".gmi": g.text,
        ".html": h.text,
        ".warnings.txt": _warnings_text(g.warnings) + "".join(f"{w.line}:{w.code}:{w.severity}:web\n" for w in h.warnings),
    }
    if request.config.getoption("--update-goldens"):
        for ext, text in outputs.items():
            (CORPUS / f"{name}{ext}").write_text(text, encoding="utf-8", newline="\n")
        return
    for ext, text in outputs.items():
        golden = CORPUS / f"{name}{ext}"
        assert golden.exists(), f"missing golden {golden.name}; run pytest --update-goldens"
        assert text == golden.read_text(encoding="utf-8"), f"{golden.name} differs; run pytest --update-goldens and review"
