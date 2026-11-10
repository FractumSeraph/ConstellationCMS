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


PHONE_PAGES = [
    "/",
    "/sites/demo",
    "/sites/demo/posts",
    "/sites/demo/pages",
    "/sites/demo/taxonomies",
    "/sites/demo/settings",
    "/sites/demo/theme",
    "/sites/demo/publish",
    "/sites/demo/analytics",
    "/servers",
    "/guide",
    "/doctor",
    "/account",
]


@pytest.mark.skipif(os.environ.get("CONSTELLATION_E2E", "auto") == "off", reason="e2e disabled")
def test_studio_on_a_phone(studio_server) -> None:
    """Every page fits a phone screen, and a post can be written with the Visual editor."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    import re

    base, ws = studio_server
    with sync_playwright() as pw:
        try:
            browser = (
                pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
            )
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser: {e}")
        phone = browser.new_context(
            viewport={"width": 360, "height": 740}, is_mobile=True, has_touch=True
        )
        page = phone.new_page()
        for path in PHONE_PAGES:
            page.goto(base + path)
            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            wide = page.evaluate("document.documentElement.scrollWidth")
            assert wide <= 361, f"{path} is {wide}px wide on a 360px phone"
        # The menu folds behind a button.
        page.goto(f"{base}/sites/demo/posts")
        assert not page.is_visible(".site-nav a[href='/sites/demo/settings']")
        page.tap(".nav-toggle")
        assert page.is_visible(".site-nav a[href='/sites/demo/settings']")
        # Write a post: it opens on the Write tab, in Visual mode (no Markdown to type).
        page.fill("input[name=title]", "Phone post")
        page.tap("button.primary")
        page.wait_for_url(re.compile(r".*/edit\?rel=.*"))
        page.wait_for_selector("#visual:not([hidden])")
        assert page.get_attribute("#editor-app", "data-pane") == "write"
        assert not page.is_visible(".meta-panel")
        page.tap("#visual")
        page.keyboard.type("Written on a phone.")
        page.tap(".editor-tabs [data-pane=details]")
        assert page.is_visible("#m-featured") and not page.is_visible("#visual")
        page.check("#m-featured")
        page.tap(".editor-tabs [data-pane=preview]")
        assert page.is_visible(".preview-bar") and not page.is_visible(".meta-panel")
        page.tap(".editor-tabs [data-pane=write]")
        page.tap("#save-now")
        page.wait_for_function(
            "() => document.querySelector('#save-state').innerText.startsWith('saved')"
        )
        files = list((ws / "sites" / "demo" / "content" / "posts").glob("*phone-post*/index.md"))
        assert files, "the post was created"
        text = files[0].read_text(encoding="utf-8")
        assert "Written on a phone." in text and "featured: true" in text
        browser.close()
