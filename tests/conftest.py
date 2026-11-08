from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-goldens", action="store_true", default=False,
                     help="rewrite the golden files in tests/corpus from the current renderers")
