"""Errors say what happened and what to do, and settings problems name the setting."""

from __future__ import annotations

from tests.test_studio import _csrf
from tests.test_welcome import _setup, fresh  # noqa: F401

PAGE = {"accept": "text/html,application/xhtml+xml"}


def test_error_page_says_what_happened_and_what_to_do(fresh) -> None:  # noqa: F811
    client, state, ws = fresh
    _setup(client)
    r = client.get("/sites/nope", headers=PAGE)
    assert r.status_code == 404 and "<h1>Not found</h1>" in r.text
    assert "What happened:" in r.text and "no site called" in r.text
    assert "What to do:" in r.text and "renamed, moved or deleted" in r.text
    # An address that doesn't exist at all gets the same kind of page.
    r = client.get("/no/such/page", headers=PAGE)
    assert r.status_code == 404 and "There is no page at this address." in r.text
    # Buttons that update part of a page get a short note instead of a whole page.
    r = client.get("/sites/nope", headers={**PAGE, "hx-request": "true"})
    assert r.status_code == 404 and r.text.startswith('<p class="error" role="alert">')
    # Programs (no HTML wanted) still get the plain answer.
    assert client.get("/sites/nope").json()["detail"].startswith("There is no site called")


def test_out_of_date_form(fresh) -> None:  # noqa: F811
    client, state, ws = fresh
    _setup(client)
    r = client.post("/account/look", data={"csrf": "old"}, headers=PAGE)
    assert r.status_code == 403 and "Reload the page and try again" in r.text


def test_settings_problems_name_the_setting(fresh) -> None:  # noqa: F811
    client, state, ws = fresh
    _setup(client)
    csrf = _csrf(client.get("/welcome").text)
    client.post("/welcome", data={"csrf": csrf, "name": "Notes"})
    r = client.post(
        "/sites/notes/settings", data={"csrf": csrf, "f.platforms.web.pagination": "ten"}
    )
    assert "Nothing was saved." in r.text and "“Posts per page” needs a whole number" in r.text
    r = client.post("/sites/notes/settings", data={"csrf": csrf, "f.menu.items": "[{oops"})
    assert "“Menu” isn" in r.text and "Check the brackets" in r.text
    r = client.post("/sites/notes/settings", data={"csrf": csrf, "toml_text": "title = "})
    assert "typing mistake on line 1" in r.text
