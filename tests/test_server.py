"""The release store, the HTTP API with the publish protocol end to end, and the Gemini listener."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.client import PublishClient, PublishError, blob_reader, release_manifest
from constellation.server.http import HttpApp
from constellation.server.install import install
from constellation.server.releases import ReleaseError, Store, release_id_for, validate_manifest

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "admin.example"}


def _manifest(tmp_path: Path):
    site = load_site(DEMO)
    build = build_site(site, now=NOW)
    return site, build, release_manifest(build, site)


@pytest.fixture
def server(tmp_path: Path):
    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="admin.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    from constellation.server.config import load_server_config

    cfg = load_server_config(config_path)
    store = Store(cfg.data)
    app = HttpApp(cfg, store)
    return cfg, store, app, admin_token


def test_manifest_validation_and_ids(tmp_path: Path) -> None:
    site, build, manifest = _manifest(tmp_path)
    validate_manifest(manifest, max_bytes=10**9)
    assert release_id_for(manifest) == release_id_for({**manifest, "built_at": "other"})
    bad = json.loads(json.dumps(manifest))
    bad["platforms"]["web"]["../etc/passwd"] = {"sha256": "0" * 64, "size": 1, "type": "x"}
    with pytest.raises(ReleaseError):
        validate_manifest(bad, max_bytes=10**9)
    with pytest.raises(ReleaseError):
        validate_manifest(manifest, max_bytes=10)


def test_store_commit_activate_prune(tmp_path: Path) -> None:
    site, build, manifest = _manifest(tmp_path)
    store = Store(tmp_path / "data").site("demo")
    rid, missing = store.stage(manifest, max_bytes=10**9)
    assert missing and store.current() is None
    reader = blob_reader(build)
    for sha in missing:
        store.put_blob(sha, reader(sha))
    with pytest.raises(ReleaseError):
        store.put_blob("a" * 64, b"wrong")
    store.commit(rid)
    assert store.current() == rid
    assert (store.current_dir("gemini") / "index.gmi").is_file()
    assert store.site_config()["hostnames"]["gemini"] == ["demo.example.org"]
    # A second, different release; then roll back; then prune keeps the active one.
    m2 = json.loads(json.dumps(manifest))
    data = b"hello"
    m2["platforms"]["web"]["hello.txt"] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": 5,
        "type": "text/plain",
    }
    rid2, missing2 = store.stage(m2, max_bytes=10**9)
    assert missing2 == [hashlib.sha256(data).hexdigest()]
    store.put_blob(missing2[0], data)
    store.commit(rid2)
    assert store.current() == rid2 and (store.current_dir("web") / "hello.txt").read_bytes() == data
    store.activate(rid)
    assert store.current() == rid
    assert store.prune(1) == [], "the newest release is kept and the active one is never pruned"
    store.activate(rid2)
    assert store.prune(1) == [rid] and store.current() == rid2
    assert store.has_blob(hashlib.sha256(data).hexdigest()), "blobs still referenced survive"
    # A release whose only blob is unique gets its blob dropped once pruned.
    m3 = json.loads(json.dumps(manifest))
    unique = b"unique"
    m3["platforms"]["web"] = {
        "u.txt": {"sha256": hashlib.sha256(unique).hexdigest(), "size": 6, "type": "text/plain"}
    }
    rid3, _ = store.stage(m3, max_bytes=10**9)
    store.put_blob(hashlib.sha256(unique).hexdigest(), unique)
    store.commit(rid3)
    store.activate(rid2)
    assert store.prune(0) == [rid3], "keep=0 drops everything but the active release"
    assert not store.has_blob(hashlib.sha256(unique).hexdigest()), "orphaned blob is dropped"
    tid, raw = store.mint_token("studio")
    assert store.verify_token(raw) and not store.verify_token("cst_nope")
    assert store.revoke_token(tid) and not store.verify_token(raw)


def test_publish_protocol_end_to_end(server, tmp_path: Path) -> None:
    cfg, store, app, admin_token = server
    site, build, manifest = _manifest(tmp_path)
    # Plain HTTP (no forwarded proto) is refused for the API.
    with TestClient(app.app, base_url="http://admin.example") as c:
        assert (
            c.get(
                "/_constellation/status", headers={"Authorization": f"Bearer {admin_token}"}
            ).status_code
            == 403
        )
    admin = PublishClient(
        "https://admin.example/_constellation",
        admin_token,
        client=TestClient(app.app, base_url="https://admin.example", headers=PROXY),
    )
    minted = admin.mint_token("demo", "test")
    token = minted["token"]
    publisher = PublishClient(
        "https://admin.example/_constellation",
        token,
        client=TestClient(app.app, base_url="https://admin.example", headers=PROXY),
    )
    # A site token cannot mint tokens or read the status.
    with pytest.raises(PublishError):
        publisher.mint_token("demo", "x")
    with pytest.raises(PublishError):
        publisher.status()
    diff, missing = publisher.plan("demo", manifest)
    assert diff["added"] and not diff["removed"] and missing
    log: list[str] = []
    report = publisher.publish("demo", manifest, blob_reader(build), progress=log.append)
    assert report.activated and report.uploaded == len(missing) and report.skipped == 0
    assert store.site("demo").current() == report.release_id
    # Idempotent: publishing again uploads nothing and yields the same release.
    report2 = publisher.publish("demo", manifest, blob_reader(build))
    assert report2.release_id == report.release_id and report2.uploaded == 0
    # The static side now serves the site on its hostname, redirects the alias, and 404s unknown hosts.
    with TestClient(app.app, base_url="https://demo.example.org") as web:
        r = web.get(
            "/posts/the-call/",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.org"},
        )
        assert r.status_code == 200 and "The Call" in r.text
        assert r.headers["cache-control"] == "public, max-age=300"
        r = web.get(
            "/posts/the-call",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.org"},
            follow_redirects=False,
        )
        assert r.status_code == 301 and r.headers["location"] == "/posts/the-call/"
        r = web.get(
            "/",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "www.demo.example.org"},
            follow_redirects=False,
        )
        assert r.status_code == 301 and r.headers["location"] == "https://demo.example.org/"
        r = web.get(
            "/nope/", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.org"}
        )
        assert r.status_code == 404 and "Not found" in r.text
        r = web.get(
            "/../secret",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.org"},
        )
        assert r.status_code == 404
        r = web.get(
            "/", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "unknown.example"}
        )
        assert r.status_code == 404 and "No site" in r.text
    # Rollback through the API and retire.
    rel = publisher.releases("demo")
    assert rel["current"] == report.release_id and len(rel["releases"]) == 1
    assert admin.activate("demo", report.release_id)["status"] == "active"
    assert admin.retire("demo")["ok"] and store.site("demo").current() is None
    admin.close()
    publisher.close()


def test_blob_hash_mismatch_and_rate_limit(server, tmp_path: Path) -> None:
    cfg, store, app, admin_token = server
    with TestClient(
        app.app,
        base_url="https://admin.example",
        headers={"Authorization": f"Bearer {admin_token}", **PROXY},
    ) as c:
        r = c.put("/_constellation/sites/demo/blobs/" + "a" * 64, content=b"x")
        assert r.status_code == 409
        cfg.limits.rate_limit_per_minute = 3
        codes = [c.get("/_constellation/status").status_code for _ in range(5)]
        assert codes[-1] == 429


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _gemini_request(port: int, url: str, *, sni: str = "demo.example.org") -> tuple[str, bytes]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        ctx.wrap_socket(raw, server_hostname=sni) as s,
    ):
        s.sendall(url.encode() + b"\r\n")
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    header, _, body = data.partition(b"\r\n")
    return header.decode(), body


def test_gemini_listener(tmp_path: Path) -> None:
    from constellation.server.app import Server
    from constellation.server.config import load_server_config

    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="admin.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    port = _free_port()
    cfg.gemini.port = port
    cfg.gemini.bind = ["127.0.0.1"]
    site, build, manifest = _manifest(tmp_path)
    store = Store(cfg.data).site("demo")
    rid, missing = store.stage(manifest, max_bytes=10**9)
    reader = blob_reader(build)
    for sha in missing:
        store.put_blob(sha, reader(sha))
    store.commit(rid)
    events: list[dict] = []
    server = Server(cfg, on_event=lambda e: events.append(e))
    loop = asyncio.new_event_loop()
    started = threading.Event()

    async def run() -> None:
        from constellation.server.gemini import GeminiServer
        from constellation.server.tls import SniTable, ensure_self_signed, server_context

        key, cert = ensure_self_signed(cfg.data, "gemini", "admin.example")
        server.sni = SniTable(server_context(cert, key))
        server.reload_gemini()
        server.gemini = GeminiServer(server.route_gemini, server.sni, on_event=server.on_event)
        await server.gemini.start(["127.0.0.1"], port)
        started.set()
        while not stop_flag:
            await asyncio.sleep(0.05)
        await server.gemini.stop()

    stop_flag = False
    thread = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    thread.start()
    assert started.wait(10)
    try:
        header, body = _gemini_request(port, "gemini://demo.example.org/posts/the-call.gmi")
        assert header == "20 text/gemini; lang=en" and b"# The Call" in body
        header, _ = _gemini_request(port, "gemini://demo.example.org/")
        assert header.startswith("20 text/gemini")
        header, _ = _gemini_request(port, "gemini://demo.example.org/posts")
        assert header == "31 gemini://demo.example.org/posts/"
        header, _ = _gemini_request(port, "gemini://demo.example.org/missing.gmi")
        assert header == "51 Not found"
        header, _ = _gemini_request(port, "gemini://other.example/")
        assert header == "53 Proxy request refused"
        header, _ = _gemini_request(port, "https://demo.example.org/")
        assert header == "53 Unsupported scheme"
        header, _ = _gemini_request(port, "gemini://demo.example.org/../../etc/passwd")
        assert header == "51 Not found"
        header, _ = _gemini_request(port, "gemini://demo.example.org/" + "a" * 1100)
        assert header.startswith("59")
        header, body = _gemini_request(port, "gemini://demo.example.org/feed.xml")
        assert header == "20 application/atom+xml"
        # SNI picks the per-host certificate.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
            ctx.wrap_socket(raw, server_hostname="demo.example.org") as s,
        ):
            from cryptography import x509

            cert = x509.load_der_x509_certificate(s.getpeercert(binary_form=True))
            assert cert.subject.rfc4514_string() == "CN=demo.example.org"
            s.sendall(b"gemini://demo.example.org/\r\n")
            s.recv(10)
    finally:
        stop_flag = True
        thread.join(timeout=10)
    assert any(e["status"] == 20 and e["site"] == "demo" for e in events)
    assert any(e["status"] == 53 for e in events)
