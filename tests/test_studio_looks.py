"""Studio looks: the first-run choice, the Account setting, the drawings, and the fixes found
while building them (database lock, preview fonts)."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from constellation.studio import looks
from constellation.studio.jobs import Job
from constellation.studio.state import State
from tests.test_studio import _csrf, studio  # noqa: F401  (pytest fixture)

PASSWORD = "correct horse battery"


def _html_attrs(html: str) -> tuple[str, str]:
    m = re.search(r'<html lang="en" data-look="([^"]*)" data-mode="([^"]*)">', html)
    assert m, html[:200]
    return m.group(1), m.group(2)


def test_first_run_offers_every_look_with_star_chart_chosen(studio) -> None:  # noqa: F811
    client, state, ws = studio
    page = client.get("/setup").text
    for look in looks.LOOKS:
        assert f'value="{look.id}"' in page and look.name in page
        assert f'data-look="{look.id}"' in page  # each choice draws its own small preview
    assert re.search(r'value="starchart"\s+checked', page)
    assert re.search(r'name="mode" value="night"\s+checked', page)
    assert _html_attrs(page) == ("starchart", "night")


def test_first_run_saves_the_chosen_look_and_mode(studio) -> None:  # noqa: F811
    client, state, ws = studio
    r = client.post(
        "/setup",
        data={"password": PASSWORD, "confirm": PASSWORD, "look": "terminal", "mode": "day"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (state.settings.look, state.settings.mode) == ("terminal", "day")
    assert _html_attrs(client.get("/").text) == ("terminal", "day")
    # It survives a restart of the studio.
    again = State(state.dir, state.cache_dir)
    assert (again.settings.look, again.settings.mode) == ("terminal", "day")
    again.close()


def test_first_run_keeps_the_choice_when_the_password_is_refused(studio) -> None:  # noqa: F811
    client, state, ws = studio
    r = client.post("/setup", data={"password": "short", "confirm": "short", "look": "observatory"})
    assert "at least 10 characters" in r.text
    assert re.search(r'value="observatory"\s+checked', r.text)
    assert not state.settings.password_hash


def test_unknown_look_at_first_run_falls_back_to_the_default(studio) -> None:  # noqa: F811
    client, state, ws = studio
    client.post(
        "/setup",
        data={"password": PASSWORD, "confirm": PASSWORD, "look": "<b>", "mode": "noon"},
    )
    assert (state.settings.look, state.settings.mode) == ("starchart", "night")


def test_account_page_changes_the_look(studio) -> None:  # noqa: F811
    client, state, ws = studio
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    page = client.get("/account").text
    assert 'action="/account/look"' in page
    csrf = _csrf(page)
    r = client.post(
        "/account/look",
        data={"csrf": csrf, "look": "observatory", "mode": "system"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _html_attrs(client.get("/account").text) == ("observatory", "system")
    assert client.post("/account/look", data={"csrf": csrf, "look": "nope"}).status_code == 400
    assert client.post("/account/look", data={"look": "terminal"}).status_code == 403
    assert state.settings.look == "observatory"


def test_studio_from_before_looks_draws_the_default(studio) -> None:  # noqa: F811
    client, state, ws = studio
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    state.settings.look = ""  # a studio.toml written before looks existed
    state.settings.mode = "night"
    assert _html_attrs(client.get("/").text) == ("starchart", "night")


def test_sites_page_draws_a_constellation_per_site_and_marks_the_current_page(studio) -> None:  # noqa: F811
    client, state, ws = studio
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    assert 'class="site-stars"' in client.get("/").text
    dash = client.get("/sites/demo").text
    assert '<a href="/sites/demo" class="current" aria-current="page">Dashboard</a>' in dash
    assert 'class="plain stars"' in dash
    assert 'href="/static/favicon.svg"' in dash and 'class="brand-mark"' in dash


def test_site_stars_are_stable_and_differ_between_sites() -> None:
    a1, a2, b = (
        looks.site_stars("fractumseraph"),
        looks.site_stars("fractumseraph"),
        looks.site_stars("fractum-faith"),
    )
    assert a1 == a2 and a1 != b
    count = len(re.findall("<circle", str(a1)))
    assert 4 <= count <= 7
    for x, y in re.findall(r'cx="([\d.]+)" cy="([\d.]+)"', str(a1)):
        assert 0 <= float(x) <= 96 and 0 <= float(y) <= 40


def _job(*lines: str, kind: str = "publish") -> Job:
    job = Job("job1", kind, "demo")
    job.log = list(lines)
    return job


def test_publish_progress_follows_the_upload_log() -> None:
    assert looks.job_progress(_job("loading demo", "building every enabled platform")) is None
    assert looks.job_progress(_job("release r: 4 blobs", kind="export")) is None
    mid = looks.job_progress(_job("release abc: 40 blobs to upload", "uploaded 25/40"))
    assert mid == {"done": 25, "total": 40, "fraction": 0.625, "active": False}
    done = looks.job_progress(
        _job("release abc: 40 blobs to upload", "uploaded 25/40", "release abc is active")
    )
    assert done is not None and done["fraction"] == 1.0 and done["active"]
    nothing_new = looks.job_progress(_job("release abc: 0 blobs to upload"))
    assert nothing_new is not None and nothing_new["fraction"] == 1.0


def test_publish_sky_lights_stars_then_settles() -> None:
    assert str(looks.publish_sky(_job("loading demo"))) == ""
    mid = str(looks.publish_sky(_job("release abc: 40 blobs to upload", "uploaded 20/40")))
    assert 'data-lit="6"' in mid and mid.count('class="lit"') == 6 and mid.count('class="dim"') == 6
    assert "rising" in mid and "Uploading 20 of 40 files" in mid
    live = str(looks.publish_sky(_job("release abc: 40 blobs to upload", "release abc is active")))
    assert "settled" in live and live.count('class="lit"') == 12 and "Release is live" in live


def test_state_database_survives_concurrent_requests(tmp_path: Path) -> None:
    state = State(tmp_path / "state", tmp_path / "cache")
    errors: list[BaseException] = []

    def hammer(n: int) -> None:
        try:
            for i in range(300):
                state.kv_set(f"k{n}", i)
                state.kv_get(f"k{n}")
                state.audit("tick", str(i))
        except BaseException as e:  # pragma: no cover - only on failure
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state.close()
    assert errors == []


def test_preview_fonts_load_inside_the_sandboxed_editor_frame(studio) -> None:  # noqa: F811
    client, state, ws = studio
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    toml = ws / "sites" / "demo" / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('theme = "base"', 'theme = "fractum"', 1),
        encoding="utf-8",
    )
    page = client.get("/sites/demo/preview/web/").text
    # Quoted url("/...") in the theme's inline @font-face is rewritten to the preview path.
    assert 'url("/sites/demo/preview/web/assets/fractum/fonts/jetbrainsmono.woff2")' in page
    font = client.get("/sites/demo/preview/web/assets/fractum/fonts/jetbrainsmono.woff2")
    assert font.status_code == 200
    assert font.headers["access-control-allow-origin"] == "*"
    css = client.get("/sites/demo/preview/web/assets/fractum/style.css")
    assert "access-control-allow-origin" not in css.headers
