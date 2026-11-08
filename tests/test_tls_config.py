"""Milestone 6: ACME orchestration (with a fake CA), HTTPS SNI contexts, the admin config and
certificate API, and redirects riding with a release."""

from __future__ import annotations

import datetime as dt
import ssl
from pathlib import Path

from starlette.testclient import TestClient

from constellation.server.acme import ChallengeStore, Orchestrator
from constellation.server.config import load_server_config
from constellation.server.http import HttpApp
from constellation.server.install import install
from constellation.server.releases import Store
from constellation.server.tls import (
    ensure_self_signed,
    expiry,
    fingerprint,
    https_contexts,
    regenerate,
)

PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "vps1.example"}


class FakeIssuer:
    """Pretends to be a CA: it checks the challenge was published, then hands back a self-signed pair."""

    def __init__(self, tmp: Path, *, fail_for: set[str] | None = None) -> None:
        self.tmp = tmp
        self.fail_for = fail_for or set()
        self.calls: list[list[str]] = []

    def issue(self, hostnames: list[str], challenges: ChallengeStore) -> tuple[bytes, bytes]:
        self.calls.append(hostnames)
        if hostnames[0] in self.fail_for:
            raise RuntimeError("DNS does not point here yet")
        challenges.put("tok-" + hostnames[0], "tok-" + hostnames[0] + ".keyauth")
        assert challenges.get("tok-" + hostnames[0]) is not None
        challenges.pop("tok-" + hostnames[0])
        key, cert = ensure_self_signed(self.tmp / "fake", "fake", hostnames[0], years=1)
        return cert.read_bytes(), key.read_bytes()


def test_orchestrator_issues_renews_and_reports(tmp_path: Path) -> None:
    issuer = FakeIssuer(tmp_path, fail_for={"broken.example"})
    hosts = ["a.example", "broken.example"]
    changed: list[str] = []
    orch = Orchestrator(
        tmp_path / "data",
        issuer,
        ChallengeStore(),
        lambda: hosts,
        on_change=lambda: changed.append("x"),
    )
    assert orch.ensure_all() == ["a.example"]
    assert changed == ["x"] and orch.has_valid("a.example")
    report = {r["hostname"]: r for r in orch.report()}
    assert report["a.example"]["state"] == "valid" and report["broken.example"]["state"] == "error"
    assert "DNS" in report["broken.example"]["error"]
    # Nothing to do on the next pass; a certificate near expiry is renewed.
    assert orch.ensure_all() == []
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=350)
    assert orch.ensure_all(now=later) == ["a.example"]
    assert len([c for c in issuer.calls if c == ["a.example"]]) == 2
    assert (tmp_path / "data" / "certs" / "acme-status.json").is_file()


def test_https_contexts_prefer_acme_files_and_fall_back(tmp_path: Path) -> None:
    data = tmp_path / "data"
    issuer = FakeIssuer(tmp_path)
    Orchestrator(data, issuer, ChallengeStore(), lambda: ["a.example"]).ensure_all()
    default, contexts = https_contexts(
        data, ["a.example", "b.example"], fallback_host="admin.example"
    )
    assert set(contexts) == {"a.example", "b.example"}
    assert isinstance(default, ssl.SSLContext)
    assert (data / "certs" / "web" / "b.example" / "cert.pem").is_file(), (
        "self-signed fallback for hosts without ACME files"
    )
    assert (data / "certs" / "web" / "admin.example" / "cert.pem").is_file()


def test_regenerate_changes_fingerprint(tmp_path: Path) -> None:
    key, cert = ensure_self_signed(tmp_path, "gemini", "x.example")
    fp1 = fingerprint(cert)
    exp = expiry(cert)
    assert exp > dt.datetime.now(dt.UTC) + dt.timedelta(days=365 * 4)
    regenerate(tmp_path, "gemini", "x.example")
    assert fingerprint(cert) != fp1


def test_admin_config_and_cert_api(tmp_path: Path) -> None:
    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    app = HttpApp(cfg, Store(cfg.data))
    app.challenges = ChallengeStore()
    app.challenges.put("abc", "abc.key")
    headers = {"Authorization": f"Bearer {admin_token}", **PROXY}
    with TestClient(app.app, base_url="https://vps1.example") as c:
        # Challenges are served without auth, on any hostname, over plain http.
        r = c.get(
            "/.well-known/acme-challenge/abc",
            headers={"X-Forwarded-Proto": "http", "X-Forwarded-Host": "whatever.example"},
        )
        assert r.status_code == 200 and r.text == "abc.key"
        assert c.get("/.well-known/acme-challenge/nope").status_code == 404
        # Config read hides the token hash; a write changes the file and flags restarts.
        r = c.get("/_constellation/server/config", headers=headers)
        assert r.status_code == 200 and "token_hash" not in r.json()["config"]["admin"]
        r = c.put(
            "/_constellation/server/config",
            headers=headers,
            json={"changes": {"limits.keep_releases": "3", "gemini.cert_years": 2}},
        )
        assert r.json() == {"ok": True, "restart_required": False}
        r = c.put(
            "/_constellation/server/config",
            headers=headers,
            json={"changes": {"https.tls_mode": "acme", "https.acme_email": "o@example.org"}},
        )
        assert r.json()["restart_required"] is True
        reloaded = load_server_config(config_path)
        assert (
            reloaded.limits.keep_releases == 3
            and reloaded.https.tls_mode == "acme"
            and reloaded.https.acme_email == "o@example.org"
        )
        assert (
            c.put(
                "/_constellation/server/config",
                headers=headers,
                json={"changes": {"admin.token_hash": "x"}},
            ).status_code
            == 400
        )
        assert (
            c.put(
                "/_constellation/server/config",
                headers=headers,
                json={"changes": {"https.tls_mode": "bogus"}},
            ).status_code
            == 400
        )
        # Certificates: the admin hostname's web cert and, after a release, the site's Gemini cert.
        ensure_self_signed(cfg.data, "gemini", "demo.example.org")
        ensure_self_signed(cfg.data, "web", "vps1.example")
        r = c.get("/_constellation/server/certs", headers=headers)
        rows = {(x["platform"], x["hostname"]): x for x in r.json()["certificates"]}
        assert rows[("web", "vps1.example")]["kind"] == "self-signed"
        r = c.post(
            "/_constellation/server/certs/gemini/demo.example.org/regenerate", headers=headers
        )
        assert r.status_code == 200 and ":" in r.json()["fingerprint"]
        assert (
            c.post(
                "/_constellation/server/certs/web/vps1.example/regenerate", headers=headers
            ).status_code
            == 400
        )
        assert c.get("/_constellation/server/config", headers=PROXY).status_code == 401


def test_redirects_ride_with_the_release(tmp_path: Path) -> None:
    import shutil
    from datetime import UTC, datetime

    from constellation.core.build.pipeline import build_site
    from constellation.core.content import load_site
    from constellation.publish.client import PublishClient, blob_reader, release_manifest

    demo = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
    shutil.copytree(demo, tmp_path / "site")
    toml = tmp_path / "site" / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[[redirects]]\nfrom = "/old/"\nto = "/posts/the-call/"\nstatus = 302\n\n[headers]\n"X-Clacks-Overhead" = "GNU Terry Pratchett"\n',
        encoding="utf-8",
    )
    site = load_site(tmp_path / "site", site_id="demo")
    build = build_site(site, now=datetime(2026, 11, 9, tzinfo=UTC))
    manifest = release_manifest(build, site)
    assert manifest["site_config"]["redirects"] == [
        {"from": "/old/", "to": "/posts/the-call/", "status": 302}
    ]
    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    app = HttpApp(cfg, Store(cfg.data))
    client = PublishClient(
        "https://vps1.example/_constellation",
        admin_token,
        client=TestClient(app.app, base_url="https://vps1.example", headers=PROXY),
    )
    client.publish("demo", manifest, blob_reader(build))
    with TestClient(app.app, base_url="https://demo.example.org") as web:
        h = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.org"}
        r = web.get("/old/", headers=h, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/posts/the-call/"
        r = web.get("/posts/the-call/", headers=h)
        assert r.headers["x-clacks-overhead"] == "GNU Terry Pratchett" and r.headers[
            "strict-transport-security"
        ].startswith("max-age")
