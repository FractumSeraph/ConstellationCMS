"""Setting up a studio on a server from one link: installer, server status, desktop studio."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from constellation.cli import main
from constellation.server.config import load_server_config
from constellation.server.http import HttpApp
from constellation.server.install import install
from constellation.server.releases import Store
from constellation.studio import setup_code
from constellation.studio.app import create_app
from constellation.studio.state import State
from tests.test_studio import _setup, studio  # noqa: F401  (pytest fixture)

PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "admin.example"}


def _install(tmp_path: Path) -> tuple[Path, str]:
    return install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="admin.example",
        tls_mode="behind-proxy",
        write_unit=False,
        studio_hostname="Studio.Example",
    )


def test_installer_creates_the_setup_code_the_studio_uses(tmp_path: Path) -> None:
    config_path, _ = _install(tmp_path)
    cfg = load_server_config(config_path)
    state_dir = tmp_path / "data" / "studio-state"
    assert cfg.studio.hostname == "studio.example" and cfg.studio.state_dir == str(state_dir)
    code = setup_code.read(state_dir)
    assert len(setup_code.normalise(code)) == 12
    # The studio started with that state folder keeps the same code rather than making another.
    create_app(State(state_dir, tmp_path / "cache"), tmp_path / "ws")
    assert setup_code.read(state_dir) == code


def test_install_command_prints_the_setup_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "server",
            "install",
            "--no-unit",
            "--data-dir",
            str(tmp_path / "data"),
            "--admin-hostname",
            "admin.example",
            "--tls-mode",
            "behind-proxy",
            "--with-studio",
            "studio.example",
        ]
    )
    out = capsys.readouterr().out
    code = setup_code.read(tmp_path / "data" / "studio-state")
    assert rc == 0 and f"https://studio.example/setup?code={code}" in out
    rc = main(
        [
            "server",
            "proxy-snippet",
            "--caddy",
            "--config",
            str(tmp_path / "data" / "server.toml"),
            "site.example",
        ]
    )
    snippet = capsys.readouterr().out
    assert rc == 0 and "studio.example" in snippet and "site.example" in snippet


def test_server_status_hands_out_the_link_until_it_is_used(tmp_path: Path) -> None:
    config_path, admin_token = _install(tmp_path)
    cfg = load_server_config(config_path)
    app = HttpApp(cfg, Store(cfg.data))
    code = setup_code.read(tmp_path / "data" / "studio-state")
    auth = {**PROXY, "Authorization": f"Bearer {admin_token}"}
    with TestClient(app.app, base_url="https://admin.example") as c:
        assert c.get("/_constellation/status", headers=PROXY).status_code == 401  # admin only
        studio = c.get("/_constellation/status", headers=auth).json()["studio"]
        assert studio == {
            "url": "https://studio.example",
            "setup_link": f"https://studio.example/setup?code={code}",
        }
        setup_code.clear(tmp_path / "data" / "studio-state")
        assert c.get("/_constellation/status", headers=auth).json()["studio"]["setup_link"] == ""


def test_desktop_studio_shows_the_link_after_connecting(
    studio,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from constellation.studio import servers as srv

    client, state, ws = studio
    csrf = _setup(client)
    client.post(
        "/servers",
        data={
            "csrf": csrf,
            "id": "vps-1",
            "name": "VPS",
            "admin_url": "https://admin.example/_constellation",
        },
    )
    link = "https://studio.example/setup?code=ABCD-EFGH-JKMN"
    monkeypatch.setattr(srv, "connect_site", lambda *a, **k: "tok")
    monkeypatch.setattr(
        srv,
        "server_status",
        lambda server, token: {"studio": {"url": "https://studio.example", "setup_link": link}},
    )
    r = client.post(
        "/servers/vps-1/connect",
        data={"csrf": csrf, "site_id": "demo", "admin_token": "cst_admin_x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get("/servers").text
    assert f'href="{link}"' in page and "waiting to be set up" in page
    client.post("/servers/vps-1/studio-link/hide", data={"csrf": csrf})
    assert "waiting to be set up" not in client.get("/servers").text
    # A link that is not https is never shown.
    monkeypatch.setattr(
        srv,
        "server_status",
        lambda server, token: {"studio": {"setup_link": "javascript:alert(1)"}},
    )
    client.post(
        "/servers/vps-1/connect",
        data={"csrf": csrf, "site_id": "demo", "admin_token": "cst_admin_x"},
    )
    assert "javascript:" not in client.get("/servers").text


def test_studio_command_mentions_the_code_on_first_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    rc = main(
        [
            "studio",
            "--workspace",
            str(tmp_path / "ws"),
            "--no-browser",
            "--state-dir",
            str(tmp_path / "s"),
        ]
    )
    out = capsys.readouterr().out
    code = setup_code.read(tmp_path / "s")
    assert (
        rc == 0 and "first run: open http://localhost:8787/" in out and f"/setup?code={code}" in out
    )
