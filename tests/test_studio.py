"""The studio web app through FastAPI's test client."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from constellation.studio.app import create_app
from constellation.studio.state import State
from constellation.studio.workspace import SiteEntry, load_workspace, save_workspace

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client, state, ws


def _setup(client: TestClient) -> str:
    r = client.post(
        "/setup",
        data={"password": "correct horse battery", "confirm": "correct horse battery"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/")
    assert r.status_code == 200
    return _csrf(r.text)


def _csrf(html: str) -> str:
    import re

    m = re.search(r'name="csrf" content="([^"]+)"', html)
    assert m
    return m.group(1)


def test_setup_then_sites_page(studio) -> None:
    client, state, ws = studio
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/setup"
    _setup(client)
    assert state.settings.password_hash
    r = client.get("/")
    assert "Demo" in r.text


def test_login_required_without_auto_login(studio) -> None:
    client, state, ws = studio
    _setup(client)
    state.settings.auto_login_local = False
    client.cookies.clear()
    r = client.get("/sites/demo", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("/login")
    r = client.post("/login", data={"password": "wrong", "next": "/"})
    assert "Wrong password" in r.text
    r = client.post(
        "/login",
        data={"password": "correct horse battery", "next": "/sites/demo"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/sites/demo"
    assert client.get("/sites/demo").status_code == 200


def test_auto_login_is_refused_in_remote_mode_or_behind_proxy(studio) -> None:
    client, state, ws = studio
    _setup(client)
    client.cookies.clear()
    assert client.get("/", follow_redirects=False).status_code == 200  # loopback auto-login
    state.settings.trust_proxy = True
    assert client.get("/", follow_redirects=False).status_code == 307
    state.settings.trust_proxy = False
    state.settings.remote_mode = True
    assert client.get("/", follow_redirects=False).status_code == 307


def test_login_lockout(studio) -> None:
    client, state, ws = studio
    _setup(client)
    state.settings.auto_login_local = False
    client.cookies.clear()
    for _ in range(5):
        client.post("/login", data={"password": "nope", "next": "/"})
    r = client.post("/login", data={"password": "correct horse battery", "next": "/"})
    assert "Too many failures" in r.text


def test_csrf_is_enforced(studio) -> None:
    client, state, ws = studio
    _setup(client)
    r = client.post("/sites/demo/new", data={"kind": "post", "title": "No token"})
    assert r.status_code == 403
    csrf = _csrf(client.get("/sites/demo").text)
    r = client.post(
        "/sites/demo/new",
        data={"kind": "post", "title": "With token", "csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "edit?rel=" in r.headers["location"]


def test_editor_save_render_and_conflict(studio) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    rel = "content/posts/2026-10-21-the-call/index.md"
    r = client.get(f"/sites/demo/edit?rel={rel}")
    assert r.status_code == 200 and "The Call" in r.text
    import re

    sha = re.search(r'data-sha="([0-9a-f]+)"', r.text).group(1)
    body = client.get(f"/sites/demo/raw?rel={rel}").text
    assert "stark truth" in body
    headers = {"X-CSRF-Token": csrf}
    # Live render for Gemini reports the H4 rule and the image rule.
    r = client.post(
        "/sites/demo/render",
        json={
            "platform": "gemini",
            "body": "#### Deep\n\n![](x.png)\n",
            "meta": {"title": "T"},
            "slug": "t",
        },
        headers=headers,
    )
    data = r.json()
    codes = {w["code"] for w in data["warnings"]}
    assert "HEADING-TOO-DEEP" in codes and "IMG-NO-ALT" in codes
    assert "<h1>T</h1>" in data["html"] and "Deep" in data["text"]
    # Save with the right sha succeeds and returns the new sha.
    meta = {
        "title": "The Call",
        "status": "published",
        "date": "2026-10-21T18:56:00",
        "tags": "foundations, new",
    }
    r = client.post(
        "/sites/demo/save",
        json={"rel": rel, "sha": sha, "body": body + "\nAdded line.\n", "meta": meta},
        headers=headers,
    )
    assert r.status_code == 200
    new_sha = r.json()["sha"]
    text = (ws / "sites" / "demo" / rel).read_text(encoding="utf-8")
    assert "Added line." in text and "new" in text and text.startswith("---\ntitle: The Call\n")
    # Saving again with the stale sha is a conflict; force overrides.
    r = client.post(
        "/sites/demo/save",
        json={"rel": rel, "sha": sha, "body": body, "meta": meta},
        headers=headers,
    )
    assert r.status_code == 409 and r.json()["conflict"]
    r = client.post(
        "/sites/demo/save",
        json={"rel": rel, "sha": sha, "body": body, "meta": meta, "force": True},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["sha"] != new_sha


def test_preview_build_serves_pages_and_drafts(studio) -> None:
    client, state, ws = studio
    _setup(client)
    r = client.get("/sites/demo/preview/web/posts/the-call/")
    assert r.status_code == 200 and "The Call" in r.text
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"
    r = client.get("/sites/demo/preview/web/posts/a-draft/")
    assert r.status_code == 200, "drafts are visible in previews"
    r = client.get("/sites/demo/preview/gemini/posts/the-call.gmi")
    assert r.status_code == 200 and "gemtext" in r.text and "Mend what you can." in r.text


def test_upload_converts_bare_post_to_bundle(studio) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG")
    r = client.post(
        "/sites/demo/upload",
        data={"rel": "content/posts/a-draft.md", "csrf": csrf},
        files={"file": ("My Photo (1).PNG", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["rel"] == "content/posts/a-draft/index.md" and data["name"] == "my-photo-1.png"
    assert (ws / "sites" / "demo" / "content" / "posts" / "a-draft" / "my-photo-1.png").is_file()
    r = client.post(
        "/sites/demo/upload",
        data={"rel": data["rel"], "csrf": csrf},
        files={"file": ("evil.png", b"not an image", "image/png")},
    )
    assert r.status_code == 400


def test_settings_roundtrip_keeps_comments(studio) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    toml_path = ws / "sites" / "demo" / "site.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8") + "\n# a comment the owner wrote\n", encoding="utf-8"
    )
    r = client.post(
        "/sites/demo/settings",
        data={
            "csrf": csrf,
            "f.site.tagline": "New tagline",
            "f.platforms.web.pagination": "5",
            "f.platforms.web.hostnames": "a.example, b.example",
            "f.menu.items": json.dumps([{"label": "Home", "ref": "home"}]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    text = toml_path.read_text(encoding="utf-8")
    assert (
        'tagline = "New tagline"' in text
        and "pagination = 5" in text
        and "# a comment the owner wrote" in text
    )
    assert '"a.example"' in text
    r = client.post(
        "/sites/demo/settings",
        data={"csrf": csrf, "toml_text": "this is not = toml ["},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "Not saved" in r.text


def test_taxonomies_theme_publish_pages_render(studio) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    assert "Foundations" in client.get("/sites/demo/taxonomies").text
    assert "base" in client.get("/sites/demo/theme").text
    r = client.post(
        "/sites/demo/theme",
        data={"csrf": csrf, "theme": "base", "s.show_discordian_date": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "show_discordian_date = true" in (ws / "sites" / "demo" / "site.toml").read_text(
        encoding="utf-8"
    )
    assert "Export targets" in client.get("/sites/demo/publish").text


def test_export_job_from_studio(studio, tmp_path: Path) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    out = tmp_path / "exported"
    r = client.post(
        "/sites/demo/exports",
        data={
            "csrf": csrf,
            "name": "local",
            "platform": "gemini",
            "target": f"folder:{out}",
            "layout": "plain",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.post(
        "/sites/demo/export/local", data={"mode": "apply"}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200
    import re
    import time

    job_id = re.search(r'hx-get="/jobs/([a-z0-9]+)"', r.text)
    deadline = time.time() + 20
    while job_id and time.time() < deadline:
        r = client.get(f"/jobs/{job_id.group(1)}")
        if "running" not in r.text:
            break
        time.sleep(0.2)
    assert (out / "index.gmi").is_file(), r.text
    assert "applied" in r.text
    history = json.loads((ws / "sites" / "demo" / ".constellation" / "releases.json").read_text())
    assert history["releases"][-1]["platform"] == "gemini"


def test_read_only_when_another_machine_holds_the_lock(studio) -> None:
    client, state, ws = studio
    csrf = _setup(client)
    from constellation.studio import locks

    root = ws / "sites" / "demo"
    locks.write_lock(root, "other-machine", "laptop")
    r = client.get("/sites/demo")
    assert "read-only" in r.text and "laptop" in r.text
    r = client.post("/sites/demo/new", data={"kind": "post", "title": "x", "csrf": csrf})
    assert r.status_code == 423
    import json as _json
    import time as _time

    (locks.lock_dir(root) / "other-machine.json").write_text(
        _json.dumps(
            {"machine_id": "other-machine", "label": "laptop", "heartbeat": _time.time() - 3600}
        )
    )
    r = client.post("/sites/demo/take-over", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert "read-only" not in client.get("/sites/demo").text


def test_conflict_files_are_listed(studio) -> None:
    client, state, ws = studio
    _setup(client)
    (
        ws / "sites" / "demo" / "content" / "pages" / "about (conflicted copy 2026-11-08).md"
    ).write_text("---\ntitle: x\n---\n")
    assert "conflicted copy" in client.get("/sites/demo").text
