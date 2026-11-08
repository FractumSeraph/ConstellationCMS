"""End-to-end: a real browser drives the editor. Runs where Playwright's Chromium is available."""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
CHROMIUM = os.environ.get("CONSTELLATION_CHROMIUM") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def studio_server(tmp_path: Path):
    import uvicorn

    from constellation.studio.app import create_app
    from constellation.studio.state import State
    from constellation.studio.workspace import SiteEntry, load_workspace, save_workspace

    ws = tmp_path / "workspace"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    w.sites.append(SiteEntry(id="demo", name="Demo"))
    save_workspace(w)
    state = State(tmp_path / "state", tmp_path / "cache")
    from constellation.studio.auth import hash_password

    state.settings.password_hash = hash_password("correct horse battery")
    state.save_settings()
    port = _free_port()
    config = uvicorn.Config(create_app(state, ws), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", ws
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.skipif(os.environ.get("CONSTELLATION_E2E", "auto") == "off", reason="e2e disabled")
def test_editor_end_to_end(studio_server) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    base, ws = studio_server
    with sync_playwright() as pw:
        try:
            browser = (
                pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
            )
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser: {e}")
        page = browser.new_page()
        page.goto(f"{base}/sites/demo/posts")
        page.fill("input[name=title]", "Browser post")
        page.click("button.primary")
        import re

        page.wait_for_url(re.compile(r".*/edit\?rel=.*"))
        page.wait_for_selector(".cm-content")
        page.click(".cm-content")
        page.keyboard.type("#### Too deep\n\nSome *text* here.\n")
        page.check('input[name="pv"][value="gemini"]')
        page.wait_for_selector(".warning.warn", timeout=10000)
        assert "HEADING-TOO-DEEP" in page.inner_text("#warnings")
        assert "Too deep" in page.inner_text("#gemini-body")
        page.click("button.alt:has-text('Promote to ###')")
        page.wait_for_function(
            "() => document.querySelector('.cm-content').innerText.includes('### Too deep')"
        )
        page.click("#save-now")
        page.wait_for_function(
            "() => document.querySelector('#save-state').innerText.startsWith('saved')"
        )
        files = list((ws / "sites" / "demo" / "content" / "posts").glob("*browser-post*/index.md"))
        assert files and "### Too deep" in files[0].read_text(encoding="utf-8")
        browser.close()
