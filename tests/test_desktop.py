"""The desktop app: the studio in a background thread, single instance, Quit, the icon."""

from __future__ import annotations

import socket
import zipfile
from pathlib import Path

import httpx
import pytest

from constellation import desktop
from constellation.studio import selfwheel


def test_icon() -> None:
    img = desktop.icon_image(64)
    assert img.size == (64, 64)
    r, g, b, a = img.getpixel((round(14 * 64 / 24), round(12 * 64 / 24)))
    assert (r, g, b, a) == (223, 0, 255, 255)  # the magenta star


def test_pick_port_avoids_a_busy_one() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert not desktop.port_free(port)
        other = desktop.pick_port(port)
        assert other != port and desktop.port_free(other)


def test_default_workspace_is_findable() -> None:
    assert desktop.default_workspace().name == "Constellation Sites"


def test_app_runs_answers_and_quits(tmp_path: Path) -> None:
    app = desktop.DesktopApp(tmp_path / "state", tmp_path / "sites")
    assert (tmp_path / "sites").is_dir()
    app.state.settings.port = desktop.pick_port(48787)
    app.start_server()
    try:
        r = httpx.get(app.local + "healthz", timeout=5)
        assert r.status_code == 200 and r.json()["app"] == app.state.settings.machine_id
        # A second launch finds this one instead of starting another.
        assert (
            desktop.running_instance(app.state.dir, app.state.settings.machine_id)
            == f"http://localhost:{app.port}/"
        )
        assert desktop.running_instance(app.state.dir, "someone-else") == ""
        # The studio shows Quit instead of Sign out, and Quit stops the app.
        with httpx.Client(base_url=app.local, headers={"host": f"localhost:{app.port}"}) as c:
            c.post(
                "/setup",
                data={"password": "correct horse battery", "confirm": "correct horse battery"},
            )
            page = c.get("/").text
            assert 'action="/app/quit"' in page and 'action="/logout"' not in page
            import re

            csrf = re.search(r'name="csrf" content="([^"]+)"', page).group(1)  # type: ignore[union-attr]
            r = c.post("/app/quit", data={"csrf": csrf})
            assert r.status_code == 200 and "has closed" in r.text
        assert app.thread is not None
        app.thread.join(10)
        assert not app.thread.is_alive()
        assert not (app.state.dir / desktop.APP_FILE).exists()
    finally:
        app.quit()
        app.wait()
    assert desktop.running_instance(app.state.dir, app.state.settings.machine_id) == ""


def test_selfwheel_prefers_the_bundled_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import constellation

    pkg = tmp_path / "constellation"
    (pkg / "_bundled").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    wheel = pkg / "_bundled" / "constellationcms-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("constellation/__init__.py", "")
    monkeypatch.setattr(constellation, "__file__", str(pkg / "__init__.py"))
    name, data = selfwheel.build_wheel()
    assert name == wheel.name and data == wheel.read_bytes()


def test_cli_app_passes_its_own_options(capsys: pytest.CaptureFixture[str]) -> None:
    from constellation.cli import main

    with pytest.raises(SystemExit) as e:
        main(["app", "--version"])
    assert e.value.code == 0
    assert "ConstellationCMS" in capsys.readouterr().out
