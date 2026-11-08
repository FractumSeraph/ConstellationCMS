"""The studio's server registry and publish-to-server flow against an in-process server app."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

from constellation.publish import client as publish_client
from constellation.server.config import load_server_config
from constellation.server.http import HttpApp
from constellation.server.install import install
from constellation.server.releases import Store
from constellation.studio.app import create_app
from constellation.studio.state import State
from constellation.studio.workspace import SiteEntry, load_workspace, save_workspace

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "vps1.example"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The server, in process.
    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    server_app = HttpApp(cfg, Store(cfg.data))

    # Every PublishClient the studio creates talks to that app instead of the network.
    original_init = publish_client.PublishClient.__init__

    def patched_init(
        self,
        admin_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 120,
    ) -> None:
        original_init(
            self,
            admin_url,
            token,
            client=StarletteClient(server_app.app, base_url="https://vps1.example", headers=PROXY),
            timeout=timeout,
        )

    monkeypatch.setattr(publish_client.PublishClient, "__init__", patched_init)

    ws = tmp_path / "workspace"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    w.sites.append(SiteEntry(id="demo", name="Demo"))
    save_workspace(w)
    state = State(tmp_path / "state", tmp_path / "cache")
    app = create_app(state, ws)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        client.post(
            "/setup",
            data={"password": "correct horse battery", "confirm": "correct horse battery"},
            follow_redirects=False,
        )
        csrf = re.search(r'name="csrf" content="([^"]+)"', client.get("/").text).group(1)
        yield client, csrf, state, ws, admin_token, server_app


def test_register_connect_publish_rollback(world) -> None:
    client, csrf, state, ws, admin_token, server_app = world
    r = client.post(
        "/servers",
        data={
            "csrf": csrf,
            "id": "vps-1",
            "name": "First VPS",
            "admin_url": "https://vps1.example/_constellation",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "vps1.example" in client.get("/servers").text
    assert "not connected" in client.get("/servers").text
    # Connect with the admin token: a site token is minted and stored machine-locally, the site is assigned.
    r = client.post(
        "/servers/vps-1/connect",
        data={"csrf": csrf, "site_id": "demo", "admin_token": admin_token},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/servers?ok=1"
    assert state.kv_get("token:vps-1:demo", "").startswith("cst_")
    assert load_workspace(ws).site("demo").server == "vps-1"
    assert "cst_" not in (ws / "workspace.toml").read_text(), (
        "tokens never enter the synced workspace"
    )
    assert "connected" in client.get("/servers").text
    # A wrong admin token is reported, not stored.
    r = client.post(
        "/servers/vps-1/connect",
        data={"csrf": csrf, "site_id": "demo", "admin_token": "cst_admin_wrong"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]
    # Publish from the studio: plan, then apply; poll the job.
    page = client.get("/sites/demo/publish").text
    assert "Server: First VPS" in page
    r = client.post(
        "/sites/demo/publish-to-server", data={"mode": "apply"}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200
    job_id = re.search(r'hx-get="/jobs/([a-z0-9]+)"', r.text).group(1)
    deadline = time.time() + 30
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if "running" not in r.text:
            break
        time.sleep(0.2)
    assert "is active" in r.text, r.text
    store = Store(server_app.cfg.data).site("demo")
    rid = store.current()
    assert rid and (store.current_dir("web") / "posts" / "the-call" / "index.html").is_file()
    history = json.loads((ws / "sites" / "demo" / ".constellation" / "releases.json").read_text())
    assert (
        history["releases"][-1]["kind"] == "publish" and history["releases"][-1]["release"] == rid
    )
    page = client.get("/sites/demo/publish").text
    assert "Releases on the server" in page and rid in page
    # Rollback to the same release is accepted (there is only one), and forgetting the token disables publishing.
    r = client.post(
        "/sites/demo/rollback", data={"csrf": csrf, "release_id": rid}, follow_redirects=False
    )
    assert r.status_code == 303
    client.post(
        "/servers/vps-1/forget", data={"csrf": csrf, "site_id": "demo"}, follow_redirects=False
    )
    assert "no publish token" in client.get("/sites/demo/publish").text


def test_server_cli_install_and_prune(tmp_path: Path, capsys) -> None:
    from constellation.cli import main

    rc = main(
        [
            "server",
            "install",
            "--data-dir",
            str(tmp_path / "d"),
            "--admin-hostname",
            "VPS1.Example",
            "--no-unit",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cst_admin_" in out and "1965" in out
    cfg = load_server_config(tmp_path / "d" / "server.toml")
    assert cfg.admin.hostname == "vps1.example" and cfg.https.tls_mode == "behind-proxy"
    rc = main(
        [
            "server",
            "proxy-snippet",
            "--config",
            str(tmp_path / "d" / "server.toml"),
            "demo.example.org",
        ]
    )
    assert rc == 0 and "server_name demo.example.org vps1.example" in capsys.readouterr().out
    assert main(["server", "prune", "--config", str(tmp_path / "d" / "server.toml")]) == 0
