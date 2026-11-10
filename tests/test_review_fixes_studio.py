"""Regressions for the post-0.1.0 review of the studio."""

from __future__ import annotations

import json
import time
from pathlib import Path

import frontmatter

from constellation.studio import totp
from constellation.studio.auth import host_is_loopback, safe_next
from tests.test_studio import _csrf, _setup, studio  # noqa: F401 - fixture

POST = "content/posts/2026-10-01-the-call/index.md"


def _site(ws: Path) -> Path:
    return ws / "sites" / "demo"


def _save(client, csrf: str, rel: str, meta: dict, body: str = "Body.\n", sha=None):  # type: ignore[no-untyped-def]
    return client.post(
        "/sites/demo/save",
        json={"rel": rel, "meta": meta, "body": body, "sha": sha},
        headers={"X-CSRF-Token": csrf},
    )


def _find_post(ws: Path) -> str:
    for p in sorted((_site(ws) / "content" / "posts").iterdir()):
        if p.is_dir() and (p / "index.md").is_file():
            return f"content/posts/{p.name}/index.md"
    raise AssertionError("no bundle post in the demo")


# ---- auth -------------------------------------------------------------------------------------
def test_auto_login_needs_a_loopback_host_header(studio) -> None:  # noqa: F811
    client, _state, _ws = studio
    _setup(client)
    client.cookies.clear()
    r = client.get("/", headers={"host": "attacker.example:8787"}, follow_redirects=False)
    # Stricter than a login redirect: until remote mode is on, other hostnames get nothing.
    assert r.status_code == 403 and "set-cookie" not in r.headers
    assert "Demo" not in r.text
    assert client.get("/", headers={"host": "localhost:8787"}).status_code == 200


def test_until_remote_mode_only_this_machine_is_answered(studio) -> None:  # noqa: F811
    client, state, _ws = studio
    # A fresh studio behind a public proxy must not offer first-run setup to strangers.
    proxied = {"host": "studio.example.org", "x-forwarded-for": "203.0.113.9"}
    r = client.get("/setup", headers=proxied, follow_redirects=False)
    assert r.status_code == 403 and "SSH tunnel" in r.text
    r = client.post(
        "/setup",
        data={"password": "attacker password", "confirm": "attacker password"},
        headers={**proxied, "origin": "http://studio.example.org"},
        follow_redirects=False,
    )
    assert r.status_code == 403 and not state.settings.password_hash
    # Through an SSH tunnel the request names localhost, so setup works.
    _setup(client)
    assert state.settings.password_hash
    # Password-only sign-in from outside is refused too, until remote mode (with TOTP) is on.
    assert (
        client.post(
            "/login", data={"password": "correct horse battery"}, headers=proxied
        ).status_code
        == 403
    )
    state.settings.remote_mode = True
    client.cookies.clear()
    r = client.get("/", headers=proxied, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("/login")


def test_cross_origin_posts_are_refused(studio) -> None:  # noqa: F811
    client, _state, _ws = studio
    csrf = _setup(client)
    r = client.post(
        "/sites",
        data={"csrf": csrf, "name": "Evil"},
        headers={"origin": "http://attacker.example"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_helpers() -> None:
    assert host_is_loopback("127.0.0.1:8787") and host_is_loopback("[::1]:8787")
    assert host_is_loopback("localhost") and not host_is_loopback("evil.example")
    assert safe_next("//evil.example/x") == "/" and safe_next("/\\evil") == "/"
    assert safe_next("/sites/demo/edit?rel=a") == "/sites/demo/edit?rel=a"


def test_login_redirect_cannot_leave_the_studio(studio) -> None:  # noqa: F811
    client, _state, _ws = studio
    _setup(client)
    r = client.get("/login?next=//evil.example/x", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_totp_codes_cannot_be_replayed(studio) -> None:  # noqa: F811
    client, state, _ws = studio
    _setup(client)
    state.settings.totp_secret = totp.new_secret()
    state.settings.auto_login = False
    state.save_settings()
    client.cookies.clear()
    code = totp.totp(state.settings.totp_secret)
    form = {"password": "correct horse battery", "code": code, "next": "/"}
    assert client.post("/login", data=form, follow_redirects=False).status_code == 303
    client.cookies.clear()
    again = client.post("/login", data=form, follow_redirects=False)
    assert again.status_code == 200 and "authenticator" in again.text
    assert totp.match(state.settings.totp_secret, code, at=time.time()) is not None


# ---- editor saves -----------------------------------------------------------------------------
def test_save_keeps_front_matter_the_form_does_not_show(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    rel = _find_post(ws)
    path = _site(ws) / rel
    doc = frontmatter.loads(path.read_text(encoding="utf-8"))
    doc.metadata.update({"lang": "de", "updated": "2026-10-02T10:00:00", "tags": ["a,b", "c"]})
    path.write_text(frontmatter.dumps(doc), encoding="utf-8")
    meta = {
        "title": doc.metadata["title"],
        "status": "published",
        "tags": "a,b, c",
        "seo": {"title": "SEO"},
    }
    r = _save(client, csrf, rel, meta)
    assert r.status_code == 200, r.text
    after = frontmatter.loads(path.read_text(encoding="utf-8")).metadata
    assert after["lang"] == "de" and str(after["updated"]).startswith("2026-10-02")
    assert after["tags"] == ["a,b", "c"]  # unchanged field keeps its original value
    assert after["seo"]["title"] == "SEO"


def test_dates_with_offsets_survive_an_unchanged_save(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    rel = _find_post(ws)
    path = _site(ws) / rel
    doc = frontmatter.loads(path.read_text(encoding="utf-8"))
    doc.metadata["date"] = "2026-10-25T23:30:00-07:00"
    path.write_text(frontmatter.dumps(doc), encoding="utf-8")
    page = client.get(f"/sites/demo/edit?rel={rel}")
    assert page.status_code == 200
    import re

    shown = re.search(r'id="m-date" type="datetime-local" value="([^"]*)"', page.text)
    assert shown
    r = _save(client, csrf, rel, {"title": doc.metadata["title"], "date": shown.group(1)})
    assert r.status_code == 200
    after = frontmatter.loads(path.read_text(encoding="utf-8")).metadata
    assert str(after["date"]) == "2026-10-25T23:30:00-07:00"


def test_date_only_values_open_in_the_editor(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    _setup(client)
    rel = _find_post(ws)
    path = _site(ws) / rel
    text = path.read_text(encoding="utf-8")
    doc = frontmatter.loads(text)
    doc.metadata["updated"] = __import__("datetime").date(2026, 11, 1)
    path.write_text(frontmatter.dumps(doc), encoding="utf-8")
    assert client.get(f"/sites/demo/edit?rel={rel}").status_code == 200


def test_empty_title_is_refused(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    rel = _find_post(ws)
    before = (_site(ws) / rel).read_text(encoding="utf-8")
    r = _save(client, csrf, rel, {"title": ""})
    assert r.status_code == 400 and "title" in r.json()["error"]
    assert (_site(ws) / rel).read_text(encoding="utf-8") == before


def test_new_post_avoids_slugs_of_older_posts(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    existing = _find_post(ws).split("/")[2][11:]
    r = client.post(
        "/sites/demo/new",
        data={"csrf": csrf, "kind": "post", "title": existing.replace("-", " ")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and f"{existing}-2" in r.headers["location"]
    assert client.get("/sites/demo/preview/web/").status_code == 200


def test_rel_must_point_at_a_post_or_page(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    r = client.post(
        "/sites/demo/upload",
        data={"csrf": csrf, "rel": "site.toml"},
        files={"file": ("a.png", b"x", "image/png")},
    )
    assert r.status_code == 400 and (_site(ws) / "site.toml").is_file()
    r = client.post("/sites/demo/trash", data={"csrf": csrf, "rel": "."}, follow_redirects=False)
    assert r.status_code == 400


def test_html_uploads_are_refused(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    r = client.post(
        "/sites/demo/upload",
        data={"csrf": csrf, "rel": _find_post(ws)},
        files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert r.status_code == 400 and "cannot be uploaded" in r.json()["error"]


# ---- settings, theme, locks -------------------------------------------------------------------
def test_settings_reject_bad_values_and_can_disable_platforms(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    toml = (_site(ws) / "site.toml").read_text(encoding="utf-8")
    r = client.post(
        "/sites/demo/settings",
        data={"csrf": csrf, "f.site.timezone": "Mars/Olympus"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "unknown timezone" in r.text
    r = client.post(
        "/sites/demo/settings",
        data={"csrf": csrf, "f.platforms.web.pagination": "lots"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "whole number" in r.text
    assert (_site(ws) / "site.toml").read_text(encoding="utf-8") == toml
    r = client.post(
        "/sites/demo/settings",
        data={"csrf": csrf, "f.platforms.gemini.enabled": "off"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    import tomllib

    data = tomllib.loads((_site(ws) / "site.toml").read_text(encoding="utf-8"))
    assert data["platforms"]["gemini"]["enabled"] is False


def test_switching_theme_does_not_write_the_old_forms_values(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    r = client.post(
        "/sites/demo/theme", data={"csrf": csrf, "theme": "paper"}, follow_redirects=False
    )
    assert r.status_code == 303
    import tomllib

    data = tomllib.loads((_site(ws) / "site.toml").read_text(encoding="utf-8"))
    assert data["site"]["theme"] == "paper"
    before = json.dumps(data.get("theme", {}), sort_keys=True, default=str)
    assert "glow" not in before


def test_take_over_removes_a_fresh_lock(studio) -> None:  # noqa: F811
    from constellation.studio import locks

    client, state, ws = studio
    csrf = _setup(client)
    site = _site(ws)
    locks.write_lock(site, "other-machine", "laptop")
    assert client.get("/sites/demo/posts").text.count("Take over") >= 1
    client.post("/sites/demo/take-over", data={"csrf": csrf}, follow_redirects=False)
    assert not [lk for lk in locks.other_locks(site, state.settings.machine_id)]


# ---- previews and exports ---------------------------------------------------------------------
def test_preview_roots_for_every_platform(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    _setup(client)
    import tomllib

    data = tomllib.loads((_site(ws) / "site.toml").read_text(encoding="utf-8"))
    for platform in ("web", "gemini", "gopher"):
        if data.get("platforms", {}).get(platform, {}).get("enabled", platform == "web"):
            r = client.get(f"/sites/demo/preview/{platform}/")
            assert r.status_code == 200, platform
    r = client.get("/sites/demo/preview/gemini/")
    assert 'href="/sites/demo/preview/gemini/' in r.text


def test_export_targets_are_validated(studio) -> None:  # noqa: F811
    client, _state, ws = studio
    csrf = _setup(client)
    bad = [
        {"name": "my folder", "target": "folder:/srv/out"},
        {"name": "ok", "target": "folder:relative/out"},
        {"name": "ok", "target": "sftp://u@h/p; rm -rf /"},
    ]
    for case in bad:
        r = client.post(
            "/sites/demo/exports",
            data={"csrf": csrf, "platform": "web", "layout": "plain", **case},
            follow_redirects=False,
        )
        assert r.status_code == 400, case
    r = client.post(
        "/sites/demo/exports",
        data={
            "csrf": csrf,
            "name": "gopher-out",
            "platform": "gopher",
            "target": f"folder:{ws / 'out'}",  # absolute on every OS
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
