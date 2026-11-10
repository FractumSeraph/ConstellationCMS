"""Stage A: computers have their own credentials, pair with short codes, and publish without tokens."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

from constellation.server import devices as devmod
from constellation.server.config import load_server_config
from constellation.server.devices import Devices, Pairings
from constellation.server.http import HttpApp
from constellation.server.install import install
from constellation.server.releases import Store
from constellation.studio import servers as srv
from constellation.studio.app import create_app
from constellation.studio.state import State
from constellation.studio.workspace import SiteEntry, load_workspace, save_workspace
from tests.test_studio_servers import PROXY, world  # noqa: F401  (pytest fixture)

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


# ---- the server's records --------------------------------------------------------------------
def test_devices_add_verify_remove(tmp_path: Path) -> None:
    devices = Devices(tmp_path)
    one, raw = devices.add("  laptop\n ")
    assert one.label == "laptop" and raw.startswith("cst_admin_d" + one.id + "_")
    assert devices.verify(raw) == one.id
    assert devices.verify(raw + "x") is None and devices.verify("cst_admin_nope") is None
    assert raw not in (tmp_path / "devices.json").read_text(encoding="utf-8")  # only a hash is kept
    assert devices.remove(one.id) and devices.verify(raw) is None and not devices.remove(one.id)


def test_pairing_codes_are_single_use_expire_and_lock_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(devmod.time, "monotonic", lambda: clock[0])
    p = Pairings()
    code = p.new()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    assert p.redeem(code.lower().replace("-", " ")) and not p.redeem(code)  # once only
    late = p.new()
    clock[0] += devmod.PAIRING_SECONDS + 1
    assert not p.redeem(late)  # expired
    for _ in range(devmod.MAX_BAD_CODES):
        p.redeem("WRONG-CODE")
    fresh = p.new()
    assert p.locked() and not p.redeem(fresh)  # paused for everyone
    clock[0] += devmod.BAD_CODE_WINDOW + 1
    assert not p.locked()


# ---- the server's API ------------------------------------------------------------------------
def _server(tmp_path: Path) -> tuple[HttpApp, str]:
    config_path, install_key = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    return HttpApp(cfg, Store(cfg.data)), install_key


def test_pairing_over_the_api(tmp_path: Path) -> None:
    app, install_key = _server(tmp_path)
    auth = lambda key: {**PROXY, "Authorization": f"Bearer {key}"}  # noqa: E731
    with StarletteClient(app.app, base_url="https://vps1.example") as c:
        assert c.post("/_constellation/pairings", headers=PROXY).status_code == 401
        code = c.post("/_constellation/pairings", headers=auth(install_key)).json()["code"]
        assert (
            c.post("/_constellation/pair", json={"code": "AAAA-BBBB"}, headers=PROXY).status_code
            == 403
        )
        made = c.post(
            "/_constellation/pair", json={"code": code, "label": "laptop"}, headers=PROXY
        ).json()
        key = made["key"]
        assert c.post("/_constellation/pair", json={"code": code}, headers=PROXY).status_code == 403
        assert c.get("/_constellation/status", headers=auth(key)).status_code == 200
        listing = c.get("/_constellation/devices", headers=auth(key)).json()
        assert listing["you"] == made["id"] and listing["install_key"] is True
        assert [d["label"] for d in listing["devices"]] == ["laptop"]
        assert "hash" not in listing["devices"][0]
        # The installation key can be retired once a computer has its own credential.
        assert c.delete("/_constellation/devices/install", headers=auth(key)).status_code == 200
        assert c.get("/_constellation/status", headers=auth(install_key)).status_code == 401
        assert c.get("/_constellation/status", headers=auth(key)).status_code == 200
        # Removing a computer stops it at once.
        assert (
            c.delete(f"/_constellation/devices/{made['id']}", headers=auth(key)).status_code == 200
        )
        assert c.get("/_constellation/status", headers=auth(key)).status_code == 401


def test_installation_key_cannot_be_retired_with_no_computer_left(tmp_path: Path) -> None:
    app, install_key = _server(tmp_path)
    with StarletteClient(app.app, base_url="https://vps1.example") as c:
        r = c.delete(
            "/_constellation/devices/install",
            headers={**PROXY, "Authorization": f"Bearer {install_key}"},
        )
        assert r.status_code == 409 and "first" in r.json()["error"]


# ---- the studio ------------------------------------------------------------------------------
def _add_server(client, csrf, secret=""):  # noqa: ANN001, ANN202
    return client.post(
        "/servers",
        data={"csrf": csrf, "name": "First VPS", "admin_url": "vps1.example", "secret": secret},
        follow_redirects=False,
    )


def test_connect_once_then_publish_without_tokens(world) -> None:  # noqa: F811
    client, csrf, state, ws, install_key, server_app = world
    r = _add_server(client, csrf, install_key)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    server = load_workspace(ws).servers[0]
    assert server.id == "first-vps" and server.admin_url == "https://vps1.example/_constellation"
    key = srv.admin_key(state, server.id)
    assert key.startswith("cst_admin_d") and key != install_key
    assert install_key not in json.dumps(
        [state.kv_get(k) for k in (srv.admin_key_name(server.id),)]
    )
    page = client.get("/servers").text
    assert "This computer is connected" in page and "Add another computer" in page
    # Publish here, then publish: the site's key is minted by itself.
    client.post(f"/servers/{server.id}/assign", data={"csrf": csrf, "site_id": "demo", "on": "1"})
    assert load_workspace(ws).site("demo").server == server.id
    assert "Publish every platform" in client.get("/sites/demo/publish").text
    from constellation.studio.jobs import Job

    job = Job(id="j", kind="publish", site="demo")
    result = srv.run_publish(job, state, ws / "sites" / "demo", "demo", server, apply=True)
    assert result["activated"] and srv.has_token(state, server.id, "demo")
    assert any("getting a publish key" in line for line in job.log)


def test_pair_a_second_computer_and_remove_it(world, tmp_path: Path) -> None:  # noqa: F811
    client, csrf, state, ws, install_key, server_app = world
    _add_server(client, csrf, install_key)
    server = load_workspace(ws).servers[0]
    code_html = client.post(
        f"/servers/{server.id}/pairing-code", headers={"X-CSRF-Token": csrf}
    ).text
    code = re.search(r'class="pairing-code"[^>]*>([A-Z0-9-]+)<', code_html).group(1)
    # The second computer: its own state, the same synced sites folder.
    state2 = State(tmp_path / "state2", tmp_path / "cache2")
    with TestClient(
        create_app(state2, ws), base_url="http://127.0.0.1", client=("127.0.0.1", 50001)
    ) as c2:
        c2.post(
            "/setup", data={"password": "another long password", "confirm": "another long password"}
        )
        csrf2 = re.search(r'name="csrf" content="([^"]+)"', c2.get("/").text).group(1)
        r = c2.post(
            f"/servers/{server.id}/connect-computer",
            data={"csrf": csrf2, "secret": code},
            follow_redirects=False,
        )
        assert "error" not in r.headers["location"]
        assert srv.admin_key(state2, server.id) and srv.admin_key(
            state2, server.id
        ) != srv.admin_key(state, server.id)
        # A used code does not work twice.
        r = c2.post(
            f"/servers/{server.id}/connect-computer",
            data={"csrf": csrf2, "secret": code},
            follow_redirects=False,
        )
        assert "error" in r.headers["location"]
    listing = client.get(f"/servers/{server.id}/computers").text
    # The header row, two computers and the installation key.
    assert listing.count("<tr>") == 4 and "this one" in listing
    other = srv.computers(state, server)["devices"][1]["id"]
    client.post(f"/servers/{server.id}/computers/{other}/remove", data={"csrf": csrf})
    assert len(srv.computers(state, server)["devices"]) == 1
    with pytest.raises(Exception, match="401|unauthorized"):
        srv.computers(state2, server)


def test_studio_on_a_server_connects_itself(tmp_path: Path) -> None:
    config_path, _ = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="cms.example.org",
        tls_mode="behind-proxy",
        write_unit=False,
        studio_hostname="studio.example.org",
    )
    connection = tmp_path / "data" / "studio-state" / "server-connection.json"
    data = json.loads(connection.read_text(encoding="utf-8"))
    assert data["admin_url"] == "https://cms.example.org/_constellation"
    ws = tmp_path / "ws"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    w.sites.append(SiteEntry(id="demo", name="Demo"))
    save_workspace(w)
    state = State(tmp_path / "data" / "studio-state", tmp_path / "cache")
    create_app(state, ws)
    server = load_workspace(ws).servers[0]
    assert server.admin_url == "https://cms.example.org/_constellation"
    assert srv.admin_key(state, server.id) == data["key"] and not connection.exists()
    cfg = load_server_config(config_path)
    assert Devices(cfg.data).verify(data["key"])  # a credential the server knows
