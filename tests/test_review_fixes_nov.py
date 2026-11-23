"""Fixes from the November 2026 review: one test per problem found."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from constellation.publish.export import (
    ExportFiles,
    FolderTarget,
    SftpTarget,
    apply_export,
    plan_export,
)
from constellation.publish.github_api import GitHubAPI, GitHubApiTarget
from constellation.server.releases import ReleaseError, SiteStore, validate_manifest
from constellation.studio import github
from constellation.studio.app import _explain
from tests.test_github import TOKEN, FakeGitHub, _make_site, _sign_in, studio  # noqa: F401
from tests.test_studio import _csrf

SHA = "0" * 64
PAGE = {"accept": "text/html,application/xhtml+xml"}


def _manifest(path: str) -> dict:
    return {"version": 1, "platforms": {"web": {path: {"sha256": SHA, "size": 1}}}}


@pytest.mark.parametrize("path", ["c:/Users/Public/x.txt", "C:x.txt", "a/b:c.html", "d:"])
def test_manifest_refuses_drive_letters_and_colons(path: str) -> None:
    # On Windows "c:/…" is an absolute path, so the release tree could be left behind.
    with pytest.raises(ReleaseError, match="unsafe path"):
        validate_manifest(_manifest(path), max_bytes=10**9)
    validate_manifest(_manifest("posts/a/index.html"), max_bytes=10**9)  # ordinary paths pass


def test_release_ids_from_the_address_are_checked(tmp_path: Path) -> None:
    store = SiteStore(tmp_path, "blog")
    store.ensure()
    (store.root / "manifest.json").write_text("{}", encoding="utf-8")  # one level up
    for rid in ("..", ".", "../releases", "ABCDEF0123456789", "0123"):
        with pytest.raises(ReleaseError, match="no release"):
            store.manifest(rid)
    with pytest.raises(ReleaseError):
        store.commit("..")


def test_folder_delete_stops_at_a_root_written_differently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative root never equals the resolved folders the loop walks through, so the old
    # loop went past it, removing the export folder and the empty folders above it.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parent").mkdir()
    target = FolderTarget(Path("parent/out"))
    target.write("a/b/page.html", b"x", None)
    target.delete("a/b/page.html")
    assert (tmp_path / "parent" / "out").is_dir()
    assert not (tmp_path / "parent" / "out" / "a").exists()  # empty folders inside still go


def test_sftp_paths_stay_inside_the_root() -> None:
    target = object.__new__(SftpTarget)  # no connection needed to check paths
    target.remote_root = "/srv/out"
    assert target._remote("a/b.html") == "/srv/out/a/b.html"
    for bad in ("../out2/x.html", "../outside", "../../etc/passwd"):
        with pytest.raises(ValueError, match="escapes"):
            target._remote(bad)
    target.remote_root = "/"
    assert target._remote("a.html") == "/a.html"


def test_github_upload_reuses_what_planning_read(tmp_path: Path) -> None:
    fake = FakeGitHub()
    fake.add_repo("site", {"README.md": b"hi"})
    picture = tmp_path / "a.png"
    picture.write_bytes(b"\x89PNG\0binary")
    files = ExportFiles()
    files.files["a.png"] = (None, picture)
    files.manifest["a.png"] = {"sha256": "x", "size": 12}
    target = GitHubApiTarget("github:Octo/site", GitHubAPI(TOKEN, client=fake.client))
    plan = plan_export(files, target)
    picture.unlink()  # reading it again would fail
    apply_export(files, target, plan)
    assert fake.files("site")["a.png"] == b"\x89PNG\0binary"


def test_github_sign_in_keeps_the_token_when_looking_up_the_name_fails(studio) -> None:  # noqa: F811
    client, state, ws, fake = studio
    csrf = _csrf(client.get("/account").text)
    client.post("/github/start", data={"csrf": csrf, "next": "/"})
    poll = {"x-csrf-token": csrf}
    assert (
        client.post("/github/poll", data={"next": "/"}, headers=poll).json()["state"] == "pending"
    )
    # GitHub hands over the token, but asking for the account name fails.
    fake.fail_next = [httpx.Response(404, json={"message": "Not Found"})]
    failed = client.post("/github/poll", data={"next": "/"}, headers=poll).json()
    assert failed["state"] == "error"
    assert github.token(state) == TOKEN  # kept: GitHub gives a token only once
    again = client.post("/github/poll", data={"next": "/"}, headers=poll).json()
    assert again["state"] == "done" and github.login(state) == "Octo"


def test_github_pages_button_with_broken_settings_explains(studio) -> None:  # noqa: F811
    client, state, ws, fake = studio
    _sign_in(client)
    site_id, csrf = _make_site(client, "Broken", "broken.example.org")
    (ws / "sites" / site_id / "site.toml").write_text("[site\ntitle = ", encoding="utf-8")
    r = client.post(f"/sites/{site_id}/github", data={"csrf": csrf, "repo": site_id})
    assert r.status_code == 400 and "data-job" not in r.text


def test_error_page_handles_odd_codes_and_keeps_headers(studio) -> None:  # noqa: F811
    client, state, ws, fake = studio
    happened, todo = _explain(StarletteHTTPException(499))
    assert "499" in happened and todo
    r = client.put("/account", headers=PAGE)
    assert r.status_code == 405 and "allow" in r.headers and "What to do:" in r.text
