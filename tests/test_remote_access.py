"""Milestone 4: TOTP, remote mode gating, and the server's studio proxy."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from constellation.studio import totp
from constellation.studio.app import create_app
from constellation.studio.state import State
from constellation.studio.workspace import SiteEntry, load_workspace, save_workspace

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


def test_totp_vectors_and_window() -> None:
    # RFC 6238 test vector (SHA-1, 8 digits truncated to 6 for our implementation) at T=59s with the ASCII key.
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp.totp(secret, at=59) == "287082"[-6:]
    assert totp.totp(secret, at=1111111109) == "081804"
    now = 1_700_000_000
    code = totp.totp(secret, at=now)
    assert totp.verify(secret, code, at=now)
    assert totp.verify(secret, code, at=now + 30), "one step of drift is accepted"
    assert not totp.verify(secret, code, at=now + 90)
    assert not totp.verify(secret, "abc", at=now)
    uri = totp.provisioning_uri(secret, "laptop")
    assert uri.startswith("otpauth://totp/ConstellationCMS:laptop?secret=")
    assert "<svg" in totp.qr_svg(uri)


@pytest.fixture
def studio(tmp_path: Path):
    ws = tmp_path / "workspace"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    w.sites.append(SiteEntry(id="demo", name="Demo"))
    save_workspace(w)
    state = State(tmp_path / "state", tmp_path / "cache")
    app = create_app(state, ws)
    with TestClient(app, base_url="https://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        client.post(
            "/setup",
            data={"password": "correct horse battery", "confirm": "correct horse battery"},
            follow_redirects=False,
        )
        csrf = re.search(r'name="csrf" content="([^"]+)"', client.get("/").text).group(1)
        yield client, csrf, state


def test_enable_totp_then_remote_mode_then_login(studio) -> None:
    client, csrf, state = studio
    # Remote mode refuses without a second factor.
    r = client.post(
        "/account/remote", data={"csrf": csrf, "enable": "on", "password": "correct horse battery"}
    )
    assert "enable it first" in r.text and not state.settings.remote_mode
    # Set up TOTP: begin shows a QR, a wrong code is refused, the right code enables it.
    r = client.post("/account/totp/begin", data={"csrf": csrf})
    assert "<svg" in r.text
    secret = re.search(r"<code>([A-Z2-7]+)</code>", r.text).group(1)
    r = client.post("/account/totp/confirm", data={"csrf": csrf, "code": "000000"})
    assert "did not match" in r.text and not state.settings.totp_secret
    r = client.post(
        "/account/totp/confirm",
        data={"csrf": csrf, "code": totp.totp(secret)},
        follow_redirects=False,
    )
    assert r.status_code == 303 and state.settings.totp_secret == secret
    # Now remote mode can be enabled; it signs everyone out and disables auto-login.
    r = client.post(
        "/account/remote",
        data={"csrf": csrf, "enable": "on", "bind": "0.0.0.0", "password": "correct horse battery"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and state.settings.remote_mode and state.settings.bind == "0.0.0.0"
    assert not state.settings.effective_auto_login()
    client.cookies.clear()
    assert client.get("/", follow_redirects=False).status_code == 307
    # Login now needs the code.
    r = client.post("/login", data={"password": "correct horse battery", "next": "/"})
    assert "authenticator code" in r.text.lower()
    r = client.post(
        "/login",
        data={
            "password": "correct horse battery",
            "code": totp.totp(secret),
            "next": "/sites/demo",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/sites/demo"
    assert "Recent posts" in client.get("/sites/demo").text
    # Disabling the second factor is refused while remote mode is on.
    csrf2 = re.search(r'name="csrf" content="([^"]+)"', client.get("/account").text).group(1)
    r = client.post(
        "/account/totp/disable", data={"csrf": csrf2, "password": "correct horse battery"}
    )
    assert "Turn remote mode off" in r.text and state.settings.totp_secret


def test_studio_cli_refuses_public_bind_in_local_mode(tmp_path: Path, capsys, monkeypatch) -> None:
    from constellation.cli import main

    rc = main(
        [
            "studio",
            "--workspace",
            str(tmp_path / "w"),
            "--bind",
            "0.0.0.0",
            "--no-browser",
            "--state-dir",
            str(tmp_path / "s"),
        ]
    )
    assert rc == 2 and "refusing" in capsys.readouterr().err


def test_server_proxies_the_studio_hostname(tmp_path: Path) -> None:
    import socket
    import threading

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient as StarletteClient

    from constellation.server.config import load_server_config
    from constellation.server.http import HttpApp
    from constellation.server.install import install
    from constellation.server.releases import Store

    # A stand-in studio that echoes the headers it received.
    async def echo(request):  # type: ignore[no-untyped-def]
        return JSONResponse(
            {
                "path": request.url.path,
                "proto": request.headers.get("x-forwarded-proto"),
                "host": request.headers.get("x-forwarded-host"),
                "for": request.headers.get("x-forwarded-for"),
            }
        )

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    upstream = uvicorn.Server(
        uvicorn.Config(
            Starlette(routes=[Route("/{path:path}", echo)]),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=upstream.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not upstream.started and time.time() < deadline:
        time.sleep(0.05)
    config_path, _ = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
        studio_hostname="studio.example",
        studio_port=port,
    )
    cfg = load_server_config(config_path)
    assert cfg.studio.enabled and cfg.studio.upstream.endswith(str(port))
    app = HttpApp(cfg, Store(cfg.data))
    try:
        with StarletteClient(app.app, base_url="https://studio.example") as c:
            r = c.get(
                "/sites/demo/posts",
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "studio.example",
                    "X-Forwarded-For": "203.0.113.9",
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert data == {
                "path": "/sites/demo/posts",
                "proto": "https",
                "host": "studio.example",
                "for": "203.0.113.9",
            }
            r = c.get(
                "/x",
                headers={"X-Forwarded-Proto": "http", "X-Forwarded-Host": "studio.example"},
                follow_redirects=False,
            )
            assert r.status_code == 301 and r.headers["location"] == "https://studio.example/x"
    finally:
        upstream.should_exit = True
        thread.join(timeout=5)
