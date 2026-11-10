"""The once-a-day check for a newer release, and the note the studio shows."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from constellation.studio import updates
from constellation.studio.app import create_app
from constellation.studio.state import State
from tests.test_welcome import _setup

RELEASE = {
    "tag_name": "v9.1.0",
    "html_url": "https://github.com/FractumSeraph/ConstellationCMS/releases/tag/v9.1.0",
    "assets": [
        {"name": "ConstellationCMS-9.1.0-Setup.exe", "browser_download_url": "https://x/setup.exe"},
        {
            "name": "ConstellationCMS-9.1.0-x86_64.AppImage",
            "browser_download_url": "https://x/app.AppImage",
        },
        {"name": "constellationcms_9.1.0_amd64.deb", "browser_download_url": "https://x/app.deb"},
        {
            "name": "constellationcms-9.1.0-py3-none-any.whl",
            "browser_download_url": "https://x/a.whl",
        },
    ],
}


def _client(answer: dict | None = None, status: int = 200, calls: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, json=answer or {})

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_versions() -> None:
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("0.10.0-rc1") == (0, 10, 0)
    assert updates.parse_version("nonsense") == ()
    assert updates.newer("0.2.0", "0.1.9") and updates.newer("v1.0", "0.9.9")
    assert updates.newer("0.10.0", "0.9.0")  # numbers, not text
    assert not updates.newer("0.1.0", "0.1.0") and not updates.newer("0.1", "0.1.0")
    assert not updates.newer("", "0.1.0")


def test_pick_asset() -> None:
    assets = {a["name"]: a["browser_download_url"] for a in RELEASE["assets"]}  # type: ignore[index]
    assert updates.pick_asset(assets, "windows") == "https://x/setup.exe"
    assert updates.pick_asset(assets, "appimage") == "https://x/app.AppImage"
    assert updates.pick_asset(assets, "deb") == "https://x/app.deb"
    assert updates.pick_asset(assets, "python") == ""
    assert updates.install_kind() == "python"  # the tests don't run frozen


def test_check_once_a_day_and_offline(tmp_path: Path) -> None:
    state = State(tmp_path / "s", tmp_path / "c")
    calls: list[str] = []
    latest = updates.check(state, client=_client(RELEASE, calls=calls), now=1000.0)
    assert latest and latest["version"] == "9.1.0" and calls == [updates.LATEST_API]
    # Within a day: no new request.
    updates.check(state, client=_client(RELEASE, calls=calls), now=1000.0 + 3600)
    assert len(calls) == 1

    # A day later it asks again; offline keeps the last answer.
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    kept = updates.check(
        state, client=httpx.Client(transport=httpx.MockTransport(offline)), now=1000.0 + 2 * 86400
    )
    assert kept and kept["version"] == "9.1.0"
    # No release yet (404): remembered as checked, nothing offered.
    other = State(tmp_path / "s2", tmp_path / "c2")
    assert updates.check(other, client=_client(status=404), now=5.0) is None
    assert updates.available(other) is None


def test_available_dismissed_and_off(tmp_path: Path) -> None:
    state = State(tmp_path / "s", tmp_path / "c")
    updates.check(state, client=_client(RELEASE), now=1.0)
    offer = updates.available(state, current="0.1.0")
    assert offer and offer["version"] == "9.1.0" and offer["url"] == RELEASE["html_url"]
    assert updates.available(state, current="9.1.0") is None
    state.kv_set(updates.KV_DISMISSED, "9.1.0")
    assert updates.available(state, current="0.1.0") is None
    state.kv_set(updates.KV_DISMISSED, "")
    state.kv_set(updates.KV_OFF, True)
    assert updates.available(state, current="0.1.0") is None
    calls: list[str] = []
    updates.check(state, client=_client(RELEASE, calls=calls), now=10**9)
    assert calls == []  # turned off: no requests at all


def test_the_studio_note_and_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    state = State(tmp_path / "s", tmp_path / "c")
    updates.check(state, client=_client(RELEASE), now=1.0)
    with TestClient(
        create_app(state, ws), base_url="http://127.0.0.1", client=("127.0.0.1", 5)
    ) as c:
        assert "A new version" not in c.get("/setup").text  # not before signing in
        _setup(c)
        page = c.get("/account").text
        assert "A new version of ConstellationCMS (9.1.0) is ready" in page
        assert 'id="updates"' in page and 'name="check" checked' in page
        from tests.test_studio import _csrf

        csrf = _csrf(page)
        r = c.post(
            "/updates/dismiss",
            data={"csrf": csrf, "version": "9.1.0"},
            headers={"referer": "http://127.0.0.1/account?x=1"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/account?x=1"
        assert "A new version" not in c.get("/account").text
        # Check now: asks GitHub straight away.
        monkeypatch.setattr(
            updates,
            "check",
            lambda st, force=False, **kw: st.kv_set(updates.KV_DISMISSED, "") or RELEASE,
        )
        r = c.post(
            "/updates/settings",
            data={"csrf": csrf, "check": "on", "now": "1"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/account?saved=update-found#updates"
        # Turning it off hides the note.
        c.post("/updates/settings", data={"csrf": csrf})
        page = c.get("/account").text
        assert "A new version" not in page and 'name="check" checked' not in page
        assert state.kv_get(updates.KV_OFF) is True
        # Quit only exists in the desktop app.
        assert c.post("/app/quit", data={"csrf": csrf}).status_code == 404
        assert 'action="/app/quit"' not in page
