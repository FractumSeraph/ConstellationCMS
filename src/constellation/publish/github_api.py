"""Publishing to GitHub through its web API, so neither Git nor the GitHub CLI is needed.

Three parts:

- **Signing in** with GitHub's *device flow*: GitHub shows a short code, the person types it on
  github.com, and the studio receives a token. No secret is needed, only the OAuth app's public
  client ID (see :func:`start_device_flow` and :func:`poll_device_flow`).
- **A small API client** (:class:`GitHubAPI`) that waits politely when GitHub asks it to slow
  down and turns GitHub's errors into plain sentences.
- **An export target** (:class:`GitHubApiTarget`) that writes a whole publish as one commit
  through GitHub's Git Data API: text files go inline in the new tree, other files as blobs, and
  the branch moves to the new commit at the end.
"""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from constellation.publish.export import GIT_KEEP, GITHUB_SPEC, Target

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_PAGES_HOST = "github.io"
# Text files up to this size go inline in a tree request; larger or binary files become blobs.
INLINE_LIMIT = 512 * 1024
# How much inline text one tree request carries before the next request continues the tree.
TREE_BATCH_BYTES = 4 * 1024 * 1024
TREE_BATCH_ENTRIES = 400
# Waits longer than this are not worth holding a publish open for.
LONGEST_WAIT = 300.0


class GitHubError(RuntimeError):
    """Something GitHub refused, in words a person can act on."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


# ---- signing in (device flow) ------------------------------------------------------------------


def start_device_flow(
    client_id: str, scope: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Ask GitHub for a sign-in code.

    Returns ``device_code`` (kept by the studio), ``user_code`` (shown to the person),
    ``verification_uri`` (where they type it), ``expires_in`` and ``interval`` (seconds).
    """
    own = client is None
    c = client or httpx.Client(timeout=20)
    try:
        r = c.post(
            DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": scope},
            headers={"Accept": "application/json"},
        )
        data = _json(r)
    except httpx.HTTPError as e:
        raise GitHubError(
            f"could not reach GitHub ({e.__class__.__name__}); check the internet connection"
        ) from e
    finally:
        if own:
            c.close()
    if r.status_code != 200 or "device_code" not in data:
        raise GitHubError(
            "GitHub did not start the sign-in: "
            + str(data.get("error_description") or data.get("error") or f"HTTP {r.status_code}"),
            r.status_code,
        )
    return data


def poll_device_flow(
    client_id: str, device_code: str, *, client: httpx.Client | None = None
) -> tuple[str, str]:
    """Ask once whether the person has entered the code.

    Returns ``("done", token)``, or ``(state, "")`` where state is ``pending`` (not yet),
    ``slow_down`` (ask less often), ``expired`` (the code ran out) or ``denied`` (they pressed
    Cancel on GitHub).
    """
    own = client is None
    c = client or httpx.Client(timeout=20)
    try:
        r = c.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = _json(r)
    except httpx.HTTPError:
        return ("pending", "")  # a network blip: just ask again next time
    finally:
        if own:
            c.close()
    token = data.get("access_token")
    if token:
        return ("done", str(token))
    error = str(data.get("error", ""))
    if error == "authorization_pending":
        return ("pending", "")
    if error == "slow_down":
        return ("slow_down", "")
    if error in ("expired_token", "incorrect_device_code", "token_expired"):
        return ("expired", "")
    if error == "access_denied":
        return ("denied", "")
    raise GitHubError(
        "GitHub refused the sign-in: "
        + str(data.get("error_description") or error or r.status_code)
    )


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"items": data}


# ---- the API client --------------------------------------------------------------------------


class GitHubAPI:
    """Just the calls ConstellationCMS needs, with waiting and plain error messages.

    ``say`` receives progress lines (shown in the studio's job log); ``sleep`` is replaceable
    for tests.
    """

    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        say: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        api: str = API,
    ) -> None:
        if not token:
            raise GitHubError("not signed in to GitHub; sign in on the Account page first")
        self.api = api.rstrip("/")
        self._own = client is None
        self.client = client or httpx.Client(timeout=60)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ConstellationCMS",
        }
        self.say = say or (lambda line: None)
        self.sleep = sleep

    def close(self) -> None:
        if self._own:
            self.client.close()

    def request(
        self, method: str, path: str, *, json: Any = None, allow: tuple[int, ...] = ()
    ) -> httpx.Response:
        """One API call. Statuses in ``allow`` are returned instead of raised."""
        url = path if path.startswith("http") else self.api + path
        for attempt in range(6):
            try:
                r = self.client.request(method, url, json=json, headers=self.headers)
            except httpx.HTTPError as e:
                if attempt >= 3:
                    raise GitHubError(
                        f"could not reach GitHub ({e.__class__.__name__}); check the internet connection"
                    ) from e
                self.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code < 300 or r.status_code in allow:
                return r
            wait = self._wait_for(r, attempt)
            if wait is None:
                raise self._error(r)
            if wait > LONGEST_WAIT:
                reset = r.headers.get("x-ratelimit-reset", "")
                when = (
                    time.strftime("%H:%M", time.localtime(float(reset)))
                    if reset.isdigit()
                    else "in an hour"
                )
                raise GitHubError(
                    f"GitHub's limit on requests is used up for now; publish again after {when}",
                    r.status_code,
                )
            self.say(f"GitHub asked to slow down; waiting {round(wait)} seconds")
            self.sleep(wait)
        raise self._error(r)

    def _wait_for(self, r: httpx.Response, attempt: int) -> float | None:
        """How long to wait before trying again, or None when trying again won't help."""
        if r.status_code in (403, 429):
            after = r.headers.get("retry-after", "")
            if after.isdigit():
                return float(after)
            if r.headers.get("x-ratelimit-remaining") == "0":
                reset = r.headers.get("x-ratelimit-reset", "")
                return max(1.0, float(reset) - time.time()) if reset.isdigit() else 60.0
            if "rate limit" in r.text.lower():
                return 60.0
            return None
        if r.status_code in (500, 502, 503, 504) and attempt < 3:
            return 2.0 * (attempt + 1)
        return None

    def _error(self, r: httpx.Response) -> GitHubError:
        data = _json(r)
        message = str(data.get("message") or r.reason_phrase or "")
        details = "; ".join(
            str(e.get("message") or e.get("code") or "")
            for e in data.get("errors", [])
            if isinstance(e, dict)
        )
        if details:
            message += f" ({details})"
        if r.status_code == 401:
            return GitHubError(
                "GitHub no longer accepts this computer's sign-in; sign in to GitHub again on the "
                "Account page",
                401,
            )
        if r.status_code == 403 and "scope" in message.lower():
            return GitHubError(
                "the GitHub sign-in doesn't allow this; sign out of GitHub on the Account page and "
                "sign in again",
                403,
            )
        if r.status_code == 404:
            return GitHubError(
                "GitHub says this doesn't exist, or this account can't see it: " + message, 404
            )
        return GitHubError(f"GitHub said: {message} (HTTP {r.status_code})", r.status_code)

    # ---- calls ------------------------------------------------------------------------------
    def get(self, path: str, *, allow: tuple[int, ...] = ()) -> dict[str, Any] | None:
        r = self.request("GET", path, allow=allow)
        return None if r.status_code in allow else _json(r)

    def user(self) -> dict[str, Any]:
        return self.get("/user") or {}

    def repo(self, owner: str, name: str) -> dict[str, Any] | None:
        return self.get(f"/repos/{_q(owner)}/{_q(name)}", allow=(404,))

    def create_repo(
        self, name: str, *, description: str = "", homepage: str = ""
    ) -> dict[str, Any]:
        """A new public repository with a first commit (the Git Data API needs one)."""
        r = self.request(
            "POST",
            "/user/repos",
            json={
                "name": name,
                "description": description,
                "homepage": homepage,
                "private": False,
                "has_issues": False,
                "has_projects": False,
                "has_wiki": False,
                "auto_init": True,
            },
        )
        return _json(r)

    def pages(self, owner: str, name: str) -> dict[str, Any] | None:
        return self.get(f"/repos/{_q(owner)}/{_q(name)}/pages", allow=(404,))

    def enable_pages(self, owner: str, name: str, branch: str) -> None:
        """Serve ``branch`` (its top folder) with GitHub Pages; fine if already on."""
        self.request(
            "POST",
            f"/repos/{_q(owner)}/{_q(name)}/pages",
            json={"build_type": "legacy", "source": {"branch": branch, "path": "/"}},
            allow=(409,),
        )

    def set_pages_domain(self, owner: str, name: str, cname: str) -> None:
        self.request("PUT", f"/repos/{_q(owner)}/{_q(name)}/pages", json={"cname": cname or None})

    def enforce_https(self, owner: str, name: str) -> bool:
        """Turn on "Enforce HTTPS". GitHub refuses until its certificate is ready."""
        r = self.request(
            "PUT",
            f"/repos/{_q(owner)}/{_q(name)}/pages",
            json={"https_enforced": True},
            allow=(400, 404, 422),
        )
        return r.status_code < 300


def _q(part: str) -> str:
    return quote(part, safe="")


def git_blob_sha(data: bytes) -> str:
    """The id git gives a file's content (what GitHub's trees list)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


# ---- the export target ---------------------------------------------------------------------


class GitHubApiTarget(Target):
    """A GitHub repository written through the API, as one commit per publish.

    Like the git target, the files in the branch are the record of what is published (no
    manifest file), and a README, licence, ``.gitignore``, ``CNAME`` and ``.github/`` are kept
    even though the site doesn't produce them.
    """

    hash_kind = "git"

    def __init__(self, spec: str, api: GitHubAPI) -> None:
        m = GITHUB_SPEC.match(spec)
        if not m or m.group("repo") in (".", ".."):
            raise ValueError(
                "a GitHub target looks like github:owner/repository or github:owner/repository#branch"
            )
        self.owner, self.name = m.group("owner"), m.group("repo").removesuffix(".git")
        self.api = api
        info = api.repo(self.owner, self.name)
        if info is None:
            raise GitHubError(
                f"there is no repository {self.owner}/{self.name} on GitHub that this account can see",
                404,
            )
        perms = info.get("permissions") or {}
        if perms and not perms.get("push"):
            raise GitHubError(
                f"the signed-in GitHub account can't write to {self.owner}/{self.name}", 403
            )
        self.branch = m.group("branch") or str(info.get("default_branch") or "main")
        self.base = f"/repos/{_q(self.owner)}/{_q(self.name)}"
        self.head = self._head()
        self.base_tree = ""
        self.remote: dict[str, str] = {}
        if self.head:
            commit = api.get(f"{self.base}/git/commits/{self.head}") or {}
            self.base_tree = str((commit.get("tree") or {}).get("sha", ""))
            if self.base_tree:
                tree = api.get(f"{self.base}/git/trees/{self.base_tree}?recursive=1") or {}
                if tree.get("truncated"):
                    raise GitHubError(
                        "this repository has too many files for GitHub to list in one go; "
                        "publish to a new, empty repository instead"
                    )
                self.remote = {
                    str(e["path"]): str(e["sha"])
                    for e in tree.get("tree", [])
                    if e.get("type") == "blob"
                }
        self.writes: dict[str, tuple[bytes | None, Path | None]] = {}
        self.deletes: set[str] = set()

    def describe(self) -> str:
        return f"GitHub {self.owner}/{self.name} ({self.branch})"

    def _head(self) -> str:
        r = self.api.request(
            "GET", f"{self.base}/git/ref/heads/{quote(self.branch)}", allow=(404, 409)
        )
        if r.status_code != 200:
            return ""  # an empty repository or a branch that doesn't exist yet
        return str((_json(r).get("object") or {}).get("sha", ""))

    # ---- Target --------------------------------------------------------------------------
    def local_manifest(self, files: Any) -> dict[str, dict[str, Any]]:
        """Our files described the way GitHub describes its files (git blob ids)."""
        out: dict[str, dict[str, Any]] = {}
        for path, (data, source) in files.files.items():
            raw = source.read_bytes() if source is not None else (data or b"")
            out[path] = {"sha256": "git:" + git_blob_sha(raw), "size": len(raw)}
        return out

    def read_manifest(self) -> dict[str, dict[str, Any]] | None:
        return {p: {"sha256": "git:" + sha} for p, sha in self.remote.items()}

    def exists(self, path: str) -> bool:
        return path in self.remote

    def keeps(self, path: str) -> bool:
        return bool(GIT_KEEP.match(path))

    def write(self, path: str, data: bytes | None, source: Path | None) -> None:
        if path.split("/", 1)[0].lower() == ".git":
            raise ValueError(f"refusing to write inside .git: {path}")
        self.writes[path] = (data, source)

    def delete(self, path: str) -> None:
        if path in self.remote:
            self.deletes.add(path)

    def write_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        return None  # the repository is the record

    def finish(self, summary: str) -> None:
        if not self.writes and not self.deletes:
            return
        if not self.head:
            self._bootstrap()
        entries: list[dict[str, Any]] = []
        blobs = 0
        for path, (data, source) in sorted(self.writes.items()):
            raw = source.read_bytes() if source is not None else (data or b"")
            text = _text(raw)
            if text is not None:
                entries.append({"path": path, "mode": "100644", "type": "blob", "content": text})
            else:
                r = self.api.request(
                    "POST",
                    f"{self.base}/git/blobs",
                    json={"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"},
                )
                entries.append(
                    {"path": path, "mode": "100644", "type": "blob", "sha": _json(r)["sha"]}
                )
                blobs += 1
                if blobs % 25 == 0:
                    self.api.say(f"uploaded {blobs} files")
        for path in sorted(self.deletes):
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        tree = self.base_tree
        for batch in _batches(entries):
            body: dict[str, Any] = {"tree": batch}
            if tree:
                body["base_tree"] = tree
            tree = str(_json(self.api.request("POST", f"{self.base}/git/trees", json=body))["sha"])
        commit = _json(
            self.api.request(
                "POST",
                f"{self.base}/git/commits",
                json={
                    "message": f"Publish with ConstellationCMS\n\n{summary}",
                    "tree": tree,
                    "parents": [self.head] if self.head else [],
                },
            )
        )
        sha = str(commit["sha"])
        if self.head:
            self.api.request(
                "PATCH",
                f"{self.base}/git/refs/heads/{quote(self.branch)}",
                json={"sha": sha, "force": False},
            )
        else:
            self.api.request(
                "POST",
                f"{self.base}/git/refs",
                json={"ref": f"refs/heads/{self.branch}", "sha": sha},
            )
        self.head = sha

    def _bootstrap(self) -> None:
        """Give an empty repository its first commit; GitHub's Git Data API needs one."""
        r = self.api.request("GET", f"{self.base}/git/refs/heads", allow=(404, 409))
        if r.status_code != 409:
            return  # the repository has commits; the new branch simply starts empty
        self.api.request(
            "PUT",
            f"{self.base}/contents/.nojekyll",
            json={"message": "Start the site", "content": "", "branch": self.branch},
        )
        self.head = self._head()
        commit = self.api.get(f"{self.base}/git/commits/{self.head}") or {}
        self.base_tree = str((commit.get("tree") or {}).get("sha", ""))
        self.remote[".nojekyll"] = git_blob_sha(b"")

    def close(self) -> None:
        self.api.close()


def _text(raw: bytes) -> str | None:
    """The file as text when it can travel inline in a tree request unchanged."""
    if len(raw) > INLINE_LIMIT or b"\0" in raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text.encode("utf-8") == raw else None


def _batches(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for e in entries:
        n = len(e.get("content") or "") + 200
        if current and (size + n > TREE_BATCH_BYTES or len(current) >= TREE_BATCH_ENTRIES):
            out.append(current)
            current, size = [], 0
        current.append(e)
        size += n
    if current:
        out.append(current)
    return out
