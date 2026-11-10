"""The Domain page: which DNS records a site needs, checked against a (fake) public resolver."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import httpx
import pytest
import tomlkit

from constellation.core.content import load_config
from constellation.studio import domains

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


def _client(answers: dict[tuple[str, str], list[str]]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        name, rtype = request.url.params["name"], request.url.params["type"]
        data = answers.get((name, rtype), [])
        return httpx.Response(
            200, json={"Answer": [{"type": domains.TYPES[rtype], "data": d} for d in data]}
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


def _site(tmp_path: Path, **web: object) -> Path:
    root = tmp_path / "site"
    shutil.copytree(DEMO, root)
    doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
    for k, v in web.items():
        doc["platforms"]["web"][k] = v
    (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    return root


SERVER = "https://203-0-113-10.sslip.io/_constellation"


def test_records_for_a_site_on_a_server(tmp_path: Path) -> None:
    cfg = load_config(_site(tmp_path))
    client = _client(
        {
            ("203-0-113-10.sslip.io", "A"): ["203.0.113.10"],
            ("demo.example.org", "A"): ["203.0.113.10"],
        }
    )
    report = domains.expected_records(cfg, SERVER, client=client)
    assert report.server_ip == "203.0.113.10"
    by = {r.name: r for r in report.records}
    assert by["demo.example.org"].why == "web, gemini"  # one record serves both
    assert by["demo.example.org"].ok and by["demo.example.org"].value == "203.0.113.10"
    www = by["www.demo.example.org"]
    assert not www.ok and www.found == []
    assert not report.ok


def test_wrong_and_proxied_answers(tmp_path: Path) -> None:
    cfg = load_config(_site(tmp_path))
    client = _client(
        {
            ("203-0-113-10.sslip.io", "A"): ["203.0.113.10"],
            # Cloudflare's proxy on the name the web and Gemini share: Gemini would break.
            ("demo.example.org", "A"): ["104.21.82.58"],
            # Cloudflare's proxy on a web-only name: fine.
            ("www.demo.example.org", "A"): ["172.67.153.207"],
        }
    )
    report = domains.expected_records(cfg, SERVER, client=client)
    by = {r.name: r for r in report.records}
    shared, www = by["demo.example.org"], by["www.demo.example.org"]
    assert shared.proxied and not shared.ok
    assert www.proxied and www.ok
    # A wrong address (a registrar's parking page) is reported as what it points to.
    parked = domains.expected_records(
        cfg,
        SERVER,
        client=_client(
            {
                ("203-0-113-10.sslip.io", "A"): ["203.0.113.10"],
                ("www.demo.example.org", "A"): ["198.51.100.7"],
            }
        ),
    )
    p = next(r for r in parked.records if r.name == "www.demo.example.org")
    assert not p.ok and not p.proxied and p.found == ["198.51.100.7"]


def test_records_for_github_pages(tmp_path: Path) -> None:
    root = _site(
        tmp_path, hostnames=["example.org", "www.example.org"], base_url="https://example.org"
    )
    doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
    doc["exports"] = [
        {"name": "pages", "platform": "web", "target": "github:SomeOne/site", "with_publish": True}
    ]
    (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    client = _client(
        {
            ("203-0-113-10.sslip.io", "A"): ["203.0.113.10"],
            ("example.org", "A"): list(domains.GITHUB_PAGES_IPS),
            ("www.example.org", "A"): ["185.199.108.153"],  # flattened CNAME
            ("demo.example.org", "A"): ["203.0.113.10"],
        }
    )
    report = domains.expected_records(load_config(root), SERVER, client=client)
    apex = [r for r in report.records if r.name == "example.org"]
    assert sorted(r.value for r in apex) == sorted(domains.GITHUB_PAGES_IPS) and all(
        r.ok for r in apex
    )
    www = next(r for r in report.records if r.name == "www.example.org")
    assert www.rtype == "CNAME" and www.value == "someone.github.io" and www.ok
    assert next(r for r in report.records if "gemini" in r.why).ok
    assert report.ok


def test_no_server_yet(tmp_path: Path) -> None:
    report = domains.expected_records(load_config(_site(tmp_path)), "", client=_client({}))
    assert report.records == [] and "isn't assigned to a server" in report.notes[0]


def test_domain_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from constellation.studio.app import create_app
    from constellation.studio.state import State
    from constellation.studio.workspace import (
        ServerEntry,
        SiteEntry,
        load_workspace,
        save_workspace,
    )
    from tests.test_studio import _setup

    ws = tmp_path / "ws"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    w.sites.append(SiteEntry(id="demo", name="Demo", server="vps"))
    w.servers.append(ServerEntry("vps", "VPS", SERVER))
    save_workspace(w)
    answers = {("203-0-113-10.sslip.io", "A"): ["203.0.113.10"]}
    real = domains.lookup
    monkeypatch.setattr(
        domains,
        "lookup",
        lambda name, rtype="A", client=None: real(name, rtype, client=_client(answers)),
    )
    with TestClient(
        create_app(State(tmp_path / "s", tmp_path / "c"), ws),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 5),
    ) as c:
        _setup(c)
        assert 'href="/sites/demo/domain"' in c.get("/sites/demo").text
        assert "where you bought the domain" in c.get("/sites/demo/domain").text
        part = c.get("/sites/demo/domain/check").text
        assert (
            "<code>demo.example.org</code>" in part
            and "203.0.113.10" in part
            and "not there yet" in part
        )
        assert 'hx-trigger="every 15s"' in part
        answers.update(
            {
                ("demo.example.org", "A"): ["203.0.113.10"],
                ("www.demo.example.org", "A"): ["203.0.113.10"],
            }
        )
        monkeypatch.setattr(domains, "https_works", lambda host, client=None: True)
        done = c.get("/sites/demo/domain/check").text
        assert "All records are right" in done and "every 15s" not in done
        assert re.search(r"HTTPS works too", done)
