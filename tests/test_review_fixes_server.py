"""Regressions for the post-0.1.0 review of the server and publishing."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from constellation.publish.client import PublishClient, PublishError, blob_reader
from constellation.server.releases import ReleaseError, Store, valid_hostname, validate_manifest
from tests.test_server import PROXY, _manifest, server  # noqa: F401 - fixture


def _admin(app, token: str) -> PublishClient:  # type: ignore[no-untyped-def]
    return PublishClient(
        "https://admin.example/_constellation",
        token,
        client=TestClient(app.app, base_url="https://admin.example", headers=PROXY),
    )


def _publish(app, admin: PublishClient, site: str, manifest, build) -> None:  # type: ignore[no-untyped-def]
    token = admin.mint_token(site, "t")["token"]
    pub = PublishClient(
        "https://admin.example/_constellation",
        token,
        client=TestClient(app.app, base_url="https://admin.example", headers=PROXY),
    )
    pub.publish(site, manifest, blob_reader(build))


# ---- manifests --------------------------------------------------------------------------------
def test_hostnames_must_be_real_dns_names() -> None:
    assert valid_hostname("demo.example.org") and valid_hostname("localhost")
    for bad in ("..", ".", "a..b", "-a.example", "a_b.example", "", 5):
        assert not valid_hostname(bad), bad


def test_site_config_is_validated(tmp_path: Path) -> None:
    _site, _build, manifest = _manifest(tmp_path)
    for field, value in (
        ("canonical", "oops"),
        ("headers", {"web": {"X-Bad": "a\r\nSet-Cookie: x"}}),
        ("redirects", [{"from": "x", "to": "/y"}]),
        ("language", "<script>"),
        ("hostnames", {"web": [".."]}),
    ):
        bad = json.loads(json.dumps(manifest))
        bad["site_config"][field] = value
        with pytest.raises(ReleaseError):
            validate_manifest(bad, max_bytes=10**9)


def test_file_and_folder_with_the_same_name_is_refused(tmp_path: Path) -> None:
    _site, _build, manifest = _manifest(tmp_path)
    bad = json.loads(json.dumps(manifest))
    entry = {"sha256": "0" * 64, "size": 1, "type": "text/plain"}
    bad["platforms"]["web"]["x"] = entry
    bad["platforms"]["web"]["x/y"] = entry
    with pytest.raises(ReleaseError):
        validate_manifest(bad, max_bytes=10**9)


# ---- hostnames, tokens, retire ----------------------------------------------------------------
def test_one_site_cannot_take_another_sites_hostname(server, tmp_path: Path) -> None:  # noqa: F811
    _cfg, _store, app, admin_token = server
    _site, build, manifest = _manifest(tmp_path)
    admin = _admin(app, admin_token)
    _publish(app, admin, "demo", manifest, build)
    with pytest.raises(PublishError) as e:
        _publish(app, admin, "zz-other", manifest, build)
    assert "already used by another site" in str(e.value)
    reserved = json.loads(json.dumps(manifest))
    reserved["site_config"]["hostnames"]["web"] = ["admin.example"]
    with pytest.raises(PublishError) as e:
        _publish(app, admin, "zz-third", reserved, build)
    assert "own hostname" in str(e.value)


def test_retire_revokes_tokens(server, tmp_path: Path) -> None:  # noqa: F811
    _cfg, store, app, admin_token = server
    _site, build, manifest = _manifest(tmp_path)
    admin = _admin(app, admin_token)
    token = admin.mint_token("demo", "t")["token"]
    pub = PublishClient(
        "https://admin.example/_constellation",
        token,
        client=TestClient(app.app, base_url="https://admin.example", headers=PROXY),
    )
    pub.publish("demo", manifest, blob_reader(build))
    admin.retire("demo")
    assert store.site("demo").retired()
    with pytest.raises(PublishError):
        pub.publish("demo", manifest, blob_reader(build))


def test_bad_site_ids_are_a_clean_error(server) -> None:  # noqa: F811
    _cfg, _store, app, admin_token = server
    with TestClient(app.app, base_url="https://admin.example", headers=PROXY) as c:
        r = c.get(
            "/_constellation/sites/BAD_ID/releases",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code in (400, 401)


def test_one_bad_site_config_does_not_stop_the_server(server, tmp_path: Path) -> None:  # noqa: F811
    cfg, store, _app, _token = server
    site_dir = store.site("broken").root
    site_dir.mkdir(parents=True)
    (site_dir / "site.json").write_text(
        json.dumps({"site": "broken", "site_config": {"canonical": "oops", "hostnames": {}}}),
        encoding="utf-8",
    )
    from constellation.server.http import HttpApp

    HttpApp(cfg, store)  # must not raise


def test_rate_limit_uses_the_proxys_address(server) -> None:  # noqa: F811
    cfg, _store, app, _token = server
    cfg.limits.rate_limit_per_minute = 3
    with TestClient(app.app, base_url="https://admin.example", headers=PROXY) as c:
        codes = [
            c.get(
                "/_constellation/status",
                headers={
                    "Authorization": "Bearer cst_wrong",
                    "X-Forwarded-For": f"9.9.9.{i}, 1.2.3.4",
                },
            ).status_code
            for i in range(5)
        ]
    assert 429 in codes


# ---- HTTP details -----------------------------------------------------------------------------
def test_redirects_keep_queries_and_never_go_protocol_relative(server, tmp_path: Path) -> None:  # noqa: F811
    _cfg, _store, app, admin_token = server
    _site, build, manifest = _manifest(tmp_path)
    _publish(app, _admin(app, admin_token), "demo", manifest, build)
    with TestClient(app.app, base_url="https://demo.example.org", headers=PROXY) as c:
        r = c.get(
            "https://demo.example.org//about",  # a bare "//about" would name a host
            follow_redirects=False,
            headers={"X-Forwarded-Host": "demo.example.org"},
        )
        assert r.status_code == 301 and not r.headers["location"].startswith("//")
        r = c.get(
            "/about?q=1", follow_redirects=False, headers={"X-Forwarded-Host": "demo.example.org"}
        )
        assert r.status_code == 301 and r.headers["location"].endswith("?q=1")


def test_studio_proxy_accepts_posts(server) -> None:  # noqa: F811
    cfg, _store, app, _token = server
    cfg.studio.enabled = True
    cfg.studio.hostname = "studio.example"
    cfg.studio.upstream = "http://127.0.0.1:9"  # nothing listens: expect 502, not 405
    with TestClient(
        app.app,
        base_url="https://studio.example",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "studio.example"},
    ) as c:
        assert c.post("/login", data={"password": "x"}).status_code == 502
    with TestClient(app.app, base_url="https://demo.example.org", headers=PROXY) as c:
        assert c.post("/", headers={"X-Forwarded-Host": "demo.example.org"}).status_code == 405


# ---- client, files, export --------------------------------------------------------------------
def test_publish_client_reports_unreachable_servers() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = PublishClient(
        "https://nowhere.example/_constellation",
        "cst_x",
        client=httpx.Client(
            transport=httpx.MockTransport(refuse), base_url="https://nowhere.example"
        ),
    )
    with pytest.raises(PublishError) as e:
        client.status()
    assert "cannot reach" in str(e.value)

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>")

    client = PublishClient(
        "https://x.example/_constellation",
        "cst_x",
        client=httpx.Client(transport=httpx.MockTransport(html), base_url="https://x.example"),
    )
    with pytest.raises(PublishError) as e:
        client.status()
    assert "JSON" in str(e.value)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_server_toml_and_backups_are_private(server, tmp_path: Path) -> None:  # noqa: F811
    cfg, _store, _app, _token = server
    from constellation.server.backup import backup
    from constellation.server.config import save_server_config

    path = Path(cfg.data_dir) / "server.toml"
    os.chmod(path, 0o644)
    save_server_config(path, cfg)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    archive = backup(Path(cfg.data_dir), tmp_path / "backups")
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_restore_keeps_retired_sites_retired(server, tmp_path: Path) -> None:  # noqa: F811
    cfg, store, app, admin_token = server
    _site, build, manifest = _manifest(tmp_path)
    admin = _admin(app, admin_token)
    _publish(app, admin, "demo", manifest, build)
    before = store.site("demo").current_manifest()
    admin.retire("demo")
    from constellation.server.backup import backup, restore

    archive = backup(Path(cfg.data_dir), tmp_path / "b")
    restore(archive, tmp_path / "restored")
    again = Store(tmp_path / "restored").site("demo")
    assert again.current() is None and again.retired()
    rid = before["release_id"] if before and "release_id" in before else None
    for d in again.releases.iterdir():
        assert (d / "web").is_dir()
        assert (
            again.manifest(d.name)["committed_at"]
            == store.site("demo").manifest(d.name)["committed_at"]
        )
    del rid


def test_rsync_export_never_deletes_unless_asked(tmp_path: Path) -> None:
    from constellation.publish.export import ExportFiles, export_rsync

    shim = tmp_path / "bin"
    shim.mkdir()
    log = tmp_path / "args.txt"
    (shim / "rsync").write_text(f'#!/bin/sh\necho "$@" > {log}\n', encoding="utf-8")
    (shim / "rsync").chmod(0o755)
    if sys.platform == "win32":
        pytest.skip("shell shim")
    env_path = os.environ["PATH"]
    os.environ["PATH"] = f"{shim}{os.pathsep}{env_path}"
    try:
        files = ExportFiles()
        export_rsync(files, "rsync:user@host:/srv/www/", dry_run=False)
        assert "--delete" not in log.read_text()
        export_rsync(files, "rsync:user@host:/srv/www/", dry_run=False, delete=True)
        assert "--delete" in log.read_text()
        with pytest.raises(ValueError):
            export_rsync(files, "rsync:-e sh -c evil", dry_run=True)
    finally:
        os.environ["PATH"] = env_path
    assert subprocess  # imported for parity with other export tests


def test_external_tls_mode_uses_the_operators_certificates(tmp_path: Path) -> None:
    import shutil

    from constellation.server.tls import ensure_self_signed, external_contexts

    key, cert = ensure_self_signed(tmp_path / "gen", "web", "operator.example")
    root = tmp_path / "live"
    root.mkdir()
    shutil.copyfile(cert, root / "fullchain.pem")
    shutil.copyfile(key, root / "privkey.pem")
    (root / "site.example").mkdir()
    k2, c2 = ensure_self_signed(tmp_path / "gen", "web", "site.example")
    shutil.copyfile(c2, root / "site.example" / "fullchain.pem")
    shutil.copyfile(k2, root / "site.example" / "privkey.pem")
    default, contexts = external_contexts(root, ["site.example", "other.example"])
    assert default is not None and set(contexts) == {"site.example"}
    with pytest.raises(FileNotFoundError):
        external_contexts(tmp_path / "empty", ["x.example"])


def test_installer_unit_and_proxy_snippet(tmp_path: Path) -> None:
    from constellation.server.install import install, proxy_snippet

    install(
        data_dir=tmp_path / "data",
        user="constellation",
        admin_hostname="admin.example",
        tls_mode="behind-proxy",
        exe=Path("/home/me/.local/bin/constellation"),
        unit_dir=tmp_path / "units",
    )
    unit = (tmp_path / "units" / "constellation.service").read_text(encoding="utf-8")
    assert "ProtectHome=read-only" in unit
    snippet = proxy_snippet(["demo.example.org"], 8080, "admin.example")
    assert "X-Forwarded-For $remote_addr" in snippet
