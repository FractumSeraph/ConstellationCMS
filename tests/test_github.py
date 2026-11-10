"""Sign in with GitHub and publishing to GitHub Pages, against a pretend GitHub."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import tomlkit
from fastapi.testclient import TestClient

from constellation.publish.export import ExportFiles, apply_export, plan_export
from constellation.publish.github_api import (
    GitHubAPI,
    GitHubApiTarget,
    GitHubError,
    git_blob_sha,
    poll_device_flow,
)
from constellation.studio import github
from constellation.studio.app import create_app
from constellation.studio.state import State
from tests.test_studio import _csrf
from tests.test_welcome import _setup

TOKEN = "gho_pretend"


class FakeGitHub:
    """Enough of GitHub's API for ConstellationCMS: device sign-in, repositories, Pages and the
    Git Data API, kept in memory. Trees are flat {path: blob id} maps."""

    def __init__(self, login: str = "Octo") -> None:
        self.login = login
        self.repos: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.commits: dict[str, dict[str, Any]] = {}
        self.polls = 0
        self.calls: list[str] = []
        self.fail_next: list[httpx.Response] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    # ---- helpers for tests -------------------------------------------------------------------
    def add_repo(self, name: str, files: dict[str, bytes] | None = None) -> dict[str, Any]:
        repo = {
            "name": name,
            "default_branch": "main",
            "branches": {},
            "pages": None,
            "size": 0,
            "permissions": {"push": True, "admin": True},
        }
        self.repos[name.lower()] = repo
        if files is not None:
            tree = self._store_tree({p: self._blob(d) for p, d in files.items()})
            repo["branches"]["main"] = self._commit(tree, [], "first")
            repo["size"] = 1
        return repo

    def files(self, name: str, branch: str = "main") -> dict[str, bytes]:
        repo = self.repos[name.lower()]
        tree = self.trees[self.commits[repo["branches"][branch]]["tree"]]
        return {p: self.blobs[s] for p, s in tree.items()}

    # ---- storage ---------------------------------------------------------------------------
    def _blob(self, data: bytes) -> str:
        sha = git_blob_sha(data)
        self.blobs[sha] = data
        return sha

    def _store_tree(self, flat: dict[str, str]) -> str:
        sha = "t" + str(len(self.trees) + 1)
        self.trees[sha] = dict(flat)
        return sha

    def _commit(self, tree: str, parents: list[str], message: str) -> str:
        sha = "c" + str(len(self.commits) + 1)
        self.commits[sha] = {"tree": tree, "parents": parents, "message": message}
        return sha

    # ---- HTTP ----------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append(f"{request.method} {url.path}")
        if url.host == "github.com":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            assert form["client_id"] == github.CLIENT_ID
            if url.path == "/login/device/code":
                assert form["scope"] == "public_repo"
                return httpx.Response(
                    200,
                    json={
                        "device_code": "dev123",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "expires_in": 900,
                        "interval": 5,
                    },
                )
            self.polls += 1
            if self.polls == 1:
                return httpx.Response(200, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer"})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if self.fail_next:
            return self.fail_next.pop(0)
        body = json.loads(request.content) if request.content else {}
        path, method = url.path, request.method
        if path == "/user":
            return httpx.Response(200, json={"login": self.login})
        if path == "/user/repos" and method == "POST":
            if body["name"].lower() in self.repos:
                return httpx.Response(422, json={"message": "name already exists"})
            assert body["auto_init"] is True and body["private"] is False
            repo = self.add_repo(body["name"], {"README.md": b"# " + body["name"].encode()})
            return httpx.Response(201, json=self._repo_json(repo))
        m = re.match(r"^/repos/([^/]+)/([^/]+)(/.*)?$", path)
        assert m, path
        if m.group(1) != self.login:
            return httpx.Response(404, json={"message": "Not Found"})
        repo = self.repos.get(m.group(2).lower())
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        rest = m.group(3) or ""
        if rest == "":
            return httpx.Response(200, json=self._repo_json(repo))
        if rest == "/pages":
            if method == "GET":
                return (
                    httpx.Response(200, json=repo["pages"])
                    if repo["pages"]
                    else httpx.Response(404, json={"message": "Not Found"})
                )
            if method == "POST":
                if repo["pages"]:
                    return httpx.Response(409, json={"message": "already enabled"})
                repo["pages"] = {"source": body["source"], "cname": None}
                return httpx.Response(201, json=repo["pages"])
            if method == "PUT":
                repo["pages"].update(body)
                return httpx.Response(204)
        empty = not repo["branches"]
        if rest == "/git/refs/heads" and method == "GET":
            if empty:
                return httpx.Response(409, json={"message": "Git Repository is empty."})
            return httpx.Response(200, json=[])
        if rest.startswith("/git/ref/heads/"):
            if empty:
                return httpx.Response(409, json={"message": "Git Repository is empty."})
            branch = rest[len("/git/ref/heads/") :]
            if branch not in repo["branches"]:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": repo["branches"][branch]}})
        if rest.startswith("/contents/") and method == "PUT":
            name = rest[len("/contents/") :]
            tree = self._store_tree({name: self._blob(base64.b64decode(body["content"]))})
            repo["branches"][body["branch"]] = self._commit(tree, [], body["message"])
            repo["size"] = 1
            return httpx.Response(201, json={})
        if empty and rest.startswith("/git/"):
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if rest.startswith("/git/commits/"):
            c = self.commits[rest.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"tree": {"sha": c["tree"]}})
        if rest.startswith("/git/trees/") and method == "GET":
            flat = self.trees[rest.rsplit("/", 1)[1]]
            assert url.params.get("recursive") == "1"
            entries = [{"path": p, "type": "blob", "sha": s} for p, s in flat.items()]
            return httpx.Response(200, json={"tree": entries, "truncated": False})
        if rest == "/git/blobs":
            assert body["encoding"] == "base64"
            return httpx.Response(201, json={"sha": self._blob(base64.b64decode(body["content"]))})
        if rest == "/git/trees":
            flat = dict(self.trees[body["base_tree"]]) if "base_tree" in body else {}
            for e in body["tree"]:
                if "content" in e:
                    flat[e["path"]] = self._blob(e["content"].encode("utf-8"))
                elif e["sha"] is None:
                    flat.pop(e["path"], None)
                else:
                    assert e["sha"] in self.blobs
                    flat[e["path"]] = e["sha"]
            return httpx.Response(201, json={"sha": self._store_tree(flat)})
        if rest == "/git/commits":
            sha = self._commit(body["tree"], body["parents"], body["message"])
            return httpx.Response(201, json={"sha": sha})
        if rest.startswith("/git/refs/heads/") and method == "PATCH":
            branch = rest[len("/git/refs/heads/") :]
            assert self.commits[body["sha"]]["parents"] == [repo["branches"][branch]]
            repo["branches"][branch] = body["sha"]
            return httpx.Response(200, json={})
        if rest == "/git/refs" and method == "POST":
            repo["branches"][body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        raise AssertionError(f"unexpected {method} {path}")

    def _repo_json(self, repo: dict[str, Any]) -> dict[str, Any]:
        return {k: repo[k] for k in ("name", "default_branch", "size", "permissions")}


@pytest.fixture
def studio(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = State(tmp_path / "state", tmp_path / "cache")
    fake = FakeGitHub()
    app = create_app(state, ws)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        app.state.github_client = fake.client
        _setup(client)
        yield client, state, ws, fake


def _sign_in(client: TestClient) -> None:
    csrf = _csrf(client.get("/account").text)
    r = client.post("/github/start", data={"csrf": csrf, "next": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/github/code?next=/"
    page = client.get("/github/code?next=/").text
    assert "ABCD-1234" in page and "https://github.com/login/device" in page
    poll = {"x-csrf-token": csrf}
    assert (
        client.post("/github/poll", data={"next": "/"}, headers=poll).json()["state"] == "pending"
    )
    done = client.post("/github/poll", data={"next": "/"}, headers=poll).json()
    assert done == {"state": "done", "next": "/", "interval": 5}


def _make_site(client: TestClient, name: str, web_url: str = "") -> tuple[str, str]:
    csrf = _csrf(client.get("/welcome").text)
    r = client.post(
        "/welcome", data={"csrf": csrf, "name": name, "web_url": web_url}, follow_redirects=False
    )
    site_id = r.headers["location"].split("/")[2]
    return site_id, csrf


def _wait(client: TestClient, page: str) -> str:
    job_id = re.search(r'data-job="([0-9a-f]+)"', page)
    assert job_id, page
    for _ in range(200):
        html = client.get(f"/jobs/{job_id.group(1)}").text
        if "· running" not in html:
            return html
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_sign_in_and_out(studio) -> None:
    client, state, ws, fake = studio
    account = client.get("/account").text
    assert 'id="github"' in account and "Not signed in" in account
    _sign_in(client)
    assert github.token(state) == TOKEN and github.login(state) == "Octo"
    assert github.pending(state) is None
    account = client.get("/account").text
    assert "Signed in as <strong>Octo</strong>" in account
    # The code page moves on once signed in.
    assert (
        client.get("/github/code?next=/sites", follow_redirects=False).headers["location"]
        == "/sites"
    )
    # Outside addresses are never followed.
    assert github.CLIENT_ID not in account
    csrf = _csrf(account)
    client.post("/github/signout", data={"csrf": csrf})
    assert github.token(state) == "" and github.login(state) == ""
    # The token never goes into the synced sites folder.
    assert not any(TOKEN in p.read_text(errors="ignore") for p in ws.rglob("*") if p.is_file())


def test_no_javascript_button_and_safe_next(studio) -> None:
    client, state, ws, fake = studio
    csrf = _csrf(client.get("/account").text)
    client.post("/github/start", data={"csrf": csrf, "next": "https://evil.example/"})
    r = client.post(
        "/github/poll", data={"csrf": csrf, "next": "https://evil.example/"}, follow_redirects=False
    )
    assert r.headers["location"].startswith("/github/code")  # still pending
    r = client.post("/github/poll", data={"csrf": csrf, "next": "//evil"}, follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("/") and not loc.startswith("//") and "evil" not in loc


def test_wizard_to_github_pages_without_own_domain(studio) -> None:
    client, state, ws, fake = studio
    site_id, csrf = _make_site(client, "Garden Diary")
    where = client.get(f"/welcome/{site_id}/where").text
    assert 'value="github" disabled' not in where
    r = client.post(
        f"/welcome/{site_id}/where", data={"csrf": csrf, "place": "github"}, follow_redirects=False
    )
    assert r.headers["location"] == f"/sites/{site_id}/github?wizard=1"
    page = client.get(r.headers["location"]).text
    assert "Sign in with GitHub" in page and f"/sites/{site_id}/github?wizard=1" in page
    _sign_in(client)
    page = client.get(f"/sites/{site_id}/github?wizard=1").text
    assert 'value="octo.github.io"' in page and "https://octo.github.io/" in page
    # Without an address of its own, only <account>.github.io works.
    bad = client.post(f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "garden"})
    assert bad.status_code == 400 and "octo.github.io" in bad.text
    started = client.post(
        f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "octo.github.io", "wizard": "1"}
    )
    assert f"/welcome/{site_id}/write" in started.text
    done = _wait(client, started.text)
    assert "· done" in done and "https://octo.github.io/" in done
    files = fake.files("octo.github.io")
    assert "index.html" in files and ".nojekyll" in files and "README.md" in files
    assert "CNAME" not in files
    repo = fake.repos["octo.github.io"]
    assert repo["pages"]["source"] == {"branch": "main", "path": "/"}
    assert repo["pages"]["cname"] is None
    doc = tomlkit.parse((ws / "sites" / site_id / "site.toml").read_text(encoding="utf-8"))
    assert doc["platforms"]["web"]["base_url"] == "https://octo.github.io"
    assert doc["platforms"]["web"]["hostnames"] == ["octo.github.io"]
    assert [dict(e) for e in doc["exports"]] == [
        {
            "name": "github-pages",
            "platform": "web",
            "target": "github:Octo/octo.github.io",
            "layout": "plain",
        }
    ]
    assert b"https://octo.github.io/" in files["index.html"] or b'href="/' in files["index.html"]
    # The Publish page now publishes there without git, and sends only what changed.
    publish = client.get(f"/sites/{site_id}/publish").text
    assert "Put this site on GitHub Pages" not in publish and "<strong>Octo</strong>" in publish
    before = len(fake.commits)
    same = client.post(
        f"/sites/{site_id}/export/github-pages", data={"csrf": csrf, "mode": "apply"}
    )
    log = _wait(client, same.text)
    # With no posts yet the feed carries the build time, so at most that file changes.
    assert "0 added" in log and "0 removed" in log and ", 14 unchanged" in log
    assert len(fake.commits) <= before + 1


def test_github_pages_with_own_domain_and_updates(studio) -> None:
    client, state, ws, fake = studio
    _sign_in(client)
    site_id, csrf = _make_site(client, "Notes", "notes.example.org")
    page = client.get(f"/sites/{site_id}/github").text
    assert f'value="{site_id}"' in page and "https://notes.example.org/" in page
    started = client.post(f"/sites/{site_id}/github", data={"csrf": csrf, "repo": site_id})
    assert "/domain" in started.text  # where to point the domain
    assert "· done" in _wait(client, started.text)
    files = fake.files(site_id)
    assert files["CNAME"] == b"notes.example.org\n"
    assert fake.repos[site_id]["pages"]["cname"] == "notes.example.org"
    doc = tomlkit.parse((ws / "sites" / site_id / "site.toml").read_text(encoding="utf-8"))
    assert doc["platforms"]["web"]["base_url"] == "https://notes.example.org"

    # A new post, then publish: one new commit on top, only the changed files.
    from constellation.studio import content_ops

    content_ops.create_item(ws / "sites" / site_id, "post", "Spring planting", status="published")
    head = fake.repos[site_id]["branches"]["main"]
    r = client.post(f"/sites/{site_id}/export/github-pages", data={"csrf": csrf, "mode": "apply"})
    log = _wait(client, r.text)
    assert "applied" in log and "spring-planting" in log and "0 removed" in log
    new = fake.repos[site_id]["branches"]["main"]
    assert new != head and fake.commits[new]["parents"] == [head]
    assert any("spring-planting" in p for p in fake.files(site_id))
    # Running the setup again is safe (Pages already on: GitHub answers 409).
    again = client.post(
        f"/sites/{site_id}/github", data={"csrf": csrf, "repo": site_id, "confirm": "1"}
    )
    assert "· done" in _wait(client, again.text)
    doc = tomlkit.parse((ws / "sites" / site_id / "site.toml").read_text(encoding="utf-8"))
    assert len(doc["exports"]) == 1


def test_existing_repository_needs_a_yes(studio) -> None:
    client, state, ws, fake = studio
    _sign_in(client)
    site_id, csrf = _make_site(client, "Blog", "blog.example.org")
    fake.add_repo("blog", {"README.md": b"mine", "old.html": b"old", "LICENSE": b"MIT"})
    asked = client.post(f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "blog"})
    assert "already exists on GitHub and has files" in asked.text and 'name="confirm"' in asked.text
    assert "data-job" not in asked.text
    yes = client.post(
        f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "blog", "confirm": "1"}
    )
    assert "· done" in _wait(client, yes.text)
    files = fake.files("blog")
    assert "old.html" not in files and files["README.md"] == b"mine" and "LICENSE" in files
    assert "index.html" in files
    # Bad names are refused before anything happens.
    bad = client.post(f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "no spaces"})
    assert bad.status_code == 400 and "letters, digits" in bad.text


def test_not_signed_in_publish_page_offers_sign_in(studio) -> None:
    client, state, ws, fake = studio
    site_id, csrf = _make_site(client, "Plain")
    page = client.get(f"/sites/{site_id}/publish").text
    assert (
        f"/sites/{site_id}/github" in page
        and "Sign in with GitHub</a> to publish without git" in page
    )
    r = client.post(
        f"/sites/{site_id}/github", data={"csrf": csrf, "repo": "x"}, follow_redirects=False
    )
    assert r.status_code == 303  # back to the page, which offers the sign-in


# ---- the API target on its own ---------------------------------------------------------------


def _files(entries: dict[str, bytes]) -> ExportFiles:
    out = ExportFiles()
    for path, data in entries.items():
        out.files[path] = (data, None)
        out.manifest[path] = {"sha256": "x", "size": len(data)}
    return out


def test_target_binary_files_empty_repository_and_slowing_down() -> None:
    fake = FakeGitHub()
    fake.add_repo("site")  # empty: no commits at all
    waits: list[float] = []
    said: list[str] = []
    api = GitHubAPI(TOKEN, client=fake.client, sleep=waits.append, say=said.append)
    target = GitHubApiTarget("github:Octo/site", api)
    png = b"\x89PNG\r\n\x1a\n\0\0binary"
    # Like every GitHub Pages export, the files include an empty .nojekyll.
    files = _files({"index.html": "<p>héllo</p>".encode(), "img/a.png": png, ".nojekyll": b""})
    plan = plan_export(files, target)
    assert plan.added == [".nojekyll", "img/a.png", "index.html"]
    fake.fail_next = [httpx.Response(403, headers={"retry-after": "7"}, json={"message": "slow"})]
    apply_export(files, target, plan)
    assert waits == [7.0] and any("waiting 7 seconds" in s for s in said)
    got = fake.files("site")
    assert got["img/a.png"] == png and got["index.html"] == "<p>héllo</p>".encode()
    assert ".nojekyll" in got  # the first commit an empty repository needs
    blobs = [c for c in fake.calls if c.endswith("/git/blobs")]
    assert len(blobs) == 1  # text travels inline; only the picture is a blob

    # Next time: nothing changed means no requests that write.
    target2 = GitHubApiTarget("github:Octo/site", GitHubAPI(TOKEN, client=fake.client))
    assert plan_export(files, target2).empty


def test_api_errors_are_plain() -> None:
    fake = FakeGitHub()
    api = GitHubAPI(TOKEN, client=fake.client, sleep=lambda s: None)
    with pytest.raises(GitHubError, match="no repository Octo/nope"):
        GitHubApiTarget("github:Octo/nope", api)
    fake.fail_next = [httpx.Response(401, json={"message": "Bad credentials"})]
    with pytest.raises(GitHubError, match="sign in to GitHub again"):
        api.user()
    reset = str(int(time.time()) + 3000)
    fake.fail_next = [
        httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, json={}
        )
    ]
    with pytest.raises(GitHubError, match="publish again after"):
        api.user()
    with pytest.raises(GitHubError, match="not signed in"):
        GitHubAPI("")
    fake.add_repo("theirs").update(permissions={"push": False})
    with pytest.raises(GitHubError, match="can't write"):
        GitHubApiTarget("github:Octo/theirs", api)


def test_device_flow_answers() -> None:
    def answer(payload: dict[str, Any]) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        )

    assert poll_device_flow("id", "d", client=answer({"error": "authorization_pending"})) == (
        "pending",
        "",
    )
    assert poll_device_flow("id", "d", client=answer({"error": "slow_down"})) == ("slow_down", "")
    assert poll_device_flow("id", "d", client=answer({"error": "expired_token"})) == ("expired", "")
    assert poll_device_flow("id", "d", client=answer({"error": "access_denied"})) == ("denied", "")
    assert poll_device_flow("id", "d", client=answer({"access_token": "t"})) == ("done", "t")
    with pytest.raises(GitHubError):
        poll_device_flow("id", "d", client=answer({"error": "unsupported_grant_type"}))
