"""The first-run wizard: password, make a site, choose where it goes, write the first post."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomlkit
from fastapi.testclient import TestClient

from constellation.studio.app import create_app
from constellation.studio.state import State
from constellation.studio.workspace import ServerEntry, load_workspace, save_workspace
from tests.test_studio import _csrf

PASSWORD = "correct horse battery"


@pytest.fixture
def fresh(tmp_path: Path):
    """A brand-new studio with an empty sites folder."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = State(tmp_path / "state", tmp_path / "cache")
    with TestClient(
        create_app(state, ws), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        yield client, state, ws


def _setup(client: TestClient) -> str:
    r = client.post(
        "/setup", data={"password": PASSWORD, "confirm": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303
    return str(r.headers["location"])


def test_setup_leads_into_the_wizard_and_through_it(fresh) -> None:
    client, state, ws = fresh
    assert _setup(client) == "/welcome"
    page = client.get("/welcome")
    assert page.status_code == 200 and "Make your first site" in page.text
    # Theme pictures for the built-in themes, with plain names.
    for theme in ("base", "paper", "fractum", "nebula"):
        assert f'value="{theme}"' in page.text
        assert f"/static/themes/{theme}.webp" in page.text
        assert client.get(f"/static/themes/{theme}.webp").status_code == 200
    assert "Terminal" in page.text and "data-detect-tz" in page.text
    csrf = _csrf(page.text)

    r = client.post(
        "/welcome",
        data={
            "csrf": csrf,
            "name": "Garden Diary",
            "author": "Sam",
            "theme": "paper",
            "web_url": "Garden.Example.org",
            "timezone": "America/Chicago",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/welcome/garden-diary/where"
    doc = tomlkit.parse((ws / "sites" / "garden-diary" / "site.toml").read_text(encoding="utf-8"))
    assert doc["site"]["theme"] == "paper"
    assert doc["site"]["timezone"] == "America/Chicago"
    assert doc["platforms"]["web"]["base_url"] == "https://garden.example.org"
    assert doc["platforms"]["web"]["hostnames"] == ["garden.example.org"]
    assert doc["authors"][0]["name"] == "Sam"

    where = client.get("/welcome/garden-diary/where")
    assert "Where should Garden Diary go?" in where.text
    assert "Set up a server" in where.text  # no servers yet
    assert 'value="github"' in where.text and 'value="github" disabled' not in where.text
    r = client.post(
        "/welcome/garden-diary/where",
        data={"csrf": csrf, "place": "local"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/welcome/garden-diary/write"
    assert load_workspace(ws).site("garden-diary").server == ""  # type: ignore[union-attr]

    write = client.get("/welcome/garden-diary/write")
    assert "Write your first post" in write.text
    r = client.post(
        "/welcome/garden-diary/write",
        data={"csrf": csrf, "title": "Spring planting"},
        follow_redirects=False,
    )
    loc = r.headers["location"]
    assert loc.startswith("/sites/garden-diary/edit?rel=") and loc.endswith("&mode=visual")
    assert list((ws / "sites" / "garden-diary" / "content" / "posts").glob("*spring-planting*"))
    assert client.get(loc).status_code == 200


def test_setup_goes_to_sites_when_there_are_some(tmp_path: Path) -> None:
    from tests.test_studio import DEMO

    ws = tmp_path / "workspace"
    (ws / "sites").mkdir(parents=True)
    import shutil

    shutil.copytree(DEMO, ws / "sites" / "demo")
    w = load_workspace(ws)
    from constellation.studio.workspace import SiteEntry

    w.sites.append(SiteEntry(id="demo", name="Demo"))
    save_workspace(w)
    state = State(tmp_path / "state", tmp_path / "cache")
    with TestClient(
        create_app(state, ws), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        assert _setup(client) == "/"


def test_wizard_mistakes_and_name_clashes(fresh) -> None:
    client, state, ws = fresh
    _setup(client)
    csrf = _csrf(client.get("/welcome").text)
    bad = client.post("/welcome", data={"csrf": csrf, "name": "Blog", "web_url": "not a domain"})
    assert bad.status_code == 400 and "look right" in bad.text
    assert 'value="Blog"' in bad.text  # what they typed is kept
    empty = client.post("/welcome", data={"csrf": csrf, "name": "  "})
    assert empty.status_code == 400 and "Give your site a name" in empty.text
    # An unknown theme or time zone falls back instead of failing.
    for _ in range(2):
        r = client.post(
            "/welcome",
            data={"csrf": csrf, "name": "Blog", "theme": "nope", "timezone": "Mars/Base"},
            follow_redirects=False,
        )
        assert r.status_code == 303
    assert r.headers["location"] == "/welcome/blog-2/where"  # the second one gets a number
    doc = tomlkit.parse((ws / "sites" / "blog-2" / "site.toml").read_text(encoding="utf-8"))
    assert doc["site"]["theme"] == "base" and doc["site"]["timezone"] == "UTC"
    # A name with no letters or digits still makes a usable folder name.
    r = client.post("/welcome", data={"csrf": csrf, "name": "✦✦"}, follow_redirects=False)
    assert re.fullmatch(r"/welcome/site(-\d+)?/where", r.headers["location"])
    # The wizard pages need a session and a real site.
    assert client.get("/welcome/nope/where").status_code == 404


def test_choosing_a_server(fresh) -> None:
    client, state, ws = fresh
    _setup(client)
    w = load_workspace(ws)
    w.servers.append(ServerEntry("vps", "My VPS", "https://203-0-113-10.sslip.io/_constellation"))
    save_workspace(w)
    csrf = _csrf(client.get("/welcome").text)
    client.post("/welcome", data={"csrf": csrf, "name": "Notes"})
    where = client.get("/welcome/notes/where")
    assert '<option value="vps">My VPS</option>' in where.text
    client.post("/welcome/notes/where", data={"csrf": csrf, "place": "server", "server_id": "vps"})
    assert load_workspace(ws).site("notes").server == "vps"  # type: ignore[union-attr]


def test_empty_sites_page_offers_the_wizard(fresh) -> None:
    client, state, ws = fresh
    _setup(client)
    assert 'href="/welcome"' in client.get("/").text
