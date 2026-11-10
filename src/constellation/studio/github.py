"""Sign in with GitHub, and put a site on GitHub Pages with one button.

The sign-in is GitHub's device flow: the studio shows a short code, the person types it on
github.com, and GitHub hands the studio a token. The token stays in this computer's studio
state (never in the synced sites folder) and only allows public repositories (``public_repo``).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx
import tomlkit

from constellation.core.content import load_site
from constellation.publish.export import pages_hostname
from constellation.publish.github_api import (
    GitHubAPI,
    GitHubError,
    poll_device_flow,
    start_device_flow,
)
from constellation.studio.jobs import Job
from constellation.studio.state import State
from constellation.studio.workspace import read_site_toml, set_path, write_site_toml

# The ConstellationCMS OAuth app (registered by the project owner with Device Flow on). A client
# ID is public by design; there is no client secret anywhere.
CLIENT_ID = "Ov23ligUB6ROtRGGCcJt"
SCOPE = "public_repo"
KV_TOKEN = "github:token"
KV_LOGIN = "github:login"
KV_DEVICE = "github:device"
EXPORT_NAME = "github-pages"
REPO_RX = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def token(state: State) -> str:
    return str(state.kv_get(KV_TOKEN, "") or "")


def login(state: State) -> str:
    return str(state.kv_get(KV_LOGIN, "") or "") if token(state) else ""


def sign_out(state: State) -> None:
    for key in (KV_TOKEN, KV_LOGIN, KV_DEVICE):
        state.kv_set(key, "")


def begin(state: State, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Ask GitHub for a code to show; remembered until it is used or runs out."""
    data = start_device_flow(CLIENT_ID, SCOPE, client=client)
    pending = {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "uri": data.get("verification_uri", "https://github.com/login/device"),
        "interval": int(data.get("interval", 5)),
        "expires": time.time() + int(data.get("expires_in", 900)),
    }
    state.kv_set(KV_DEVICE, pending)
    return pending


def pending(state: State) -> dict[str, Any] | None:
    p = state.kv_get(KV_DEVICE)
    if not isinstance(p, dict) or not p.get("device_code"):
        return None
    if float(p.get("expires", 0)) < time.time():
        state.kv_set(KV_DEVICE, "")
        return None
    return p


def poll(state: State, *, client: httpx.Client | None = None) -> str:
    """Ask GitHub once whether the code was entered.

    Returns ``done``, ``pending``, ``slow_down``, ``expired`` or ``denied``.
    """
    p = pending(state)
    if p is None:
        return "done" if token(state) else "expired"
    status, tok = poll_device_flow(CLIENT_ID, str(p["device_code"]), client=client)
    if status == "slow_down":
        p["interval"] = int(p.get("interval", 5)) + 5
        state.kv_set(KV_DEVICE, p)
    elif status == "done":
        api = GitHubAPI(tok, client=client)
        try:
            name = str(api.user().get("login", ""))
        finally:
            api.close()
        state.kv_set(KV_TOKEN, tok)
        state.kv_set(KV_LOGIN, name)
        state.kv_set(KV_DEVICE, "")
    elif status in ("expired", "denied"):
        state.kv_set(KV_DEVICE, "")
    return status


# ---- GitHub Pages ------------------------------------------------------------------------------


def own_domain(root: Path) -> str:
    """The site's own web address host, or "" when it has none (or a github.io one)."""
    return pages_hostname(load_site(root))


def suggested_repo(site_id: str, account: str, domain: str) -> str:
    """Sites with their own domain get a repository named after the site; sites without one
    use the account's own ``<name>.github.io`` repository, the only one GitHub serves at the
    top of an address (links in ConstellationCMS sites start at the top)."""
    return site_id if domain else f"{account.lower()}.github.io"


def check_repo_name(name: str, account: str, domain: str) -> str:
    """A reason the repository name won't work, or ""."""
    if not REPO_RX.match(name) or name in (".", "..") or name.endswith(".git"):
        return "Use only letters, digits, dots, hyphens and underscores for the repository name."
    if not domain and name.lower() != f"{account.lower()}.github.io":
        return (
            f"Without a web address of your own, the site has to go in the repository called "
            f"{account.lower()}.github.io. Or give the site its own address in Settings first."
        )
    return ""


def site_address(account: str, repo: str, domain: str) -> str:
    return f"https://{domain}/" if domain else f"https://{account.lower()}.github.io/"


def setup_pages(
    job: Job,
    state: State,
    root: Path,
    site_id: str,
    repo: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Make the repository if needed, publish the site to it, and turn on GitHub Pages.

    Running it again is safe: every step checks what is already there.
    """
    from constellation.studio.publish_ops import run_export

    tok = token(state)
    api = GitHubAPI(tok, client=client, say=job.say)
    try:
        account = str(api.user().get("login", ""))
        domain = own_domain(root)
        problem = check_repo_name(repo, account, domain)
        if problem:
            raise GitHubError(problem)
        address = site_address(account, repo, domain)
        info = api.repo(account, repo)
        if info is None:
            job.say(f"making the repository {account}/{repo} on GitHub")
            info = api.create_repo(
                repo,
                description=f"{load_site(root).config.site.title} (made with ConstellationCMS)",
                homepage=address,
            )
        else:
            job.say(f"using the repository {account}/{repo}")
        branch = str(info.get("default_branch") or "main")
        target = f"github:{account}/{repo}"
        _remember_export(root, target, address if not domain else "")
        job.say("publishing the site")
        spec = {"name": EXPORT_NAME, "platform": "web", "target": target, "layout": "plain"}
        run_export(job, root, site_id, spec, apply=True, github_token=tok, client=client)
        job.say("turning on GitHub Pages")
        api.enable_pages(account, repo, branch)
        if domain:
            job.say(f"setting the custom domain {domain}")
            api.set_pages_domain(account, repo, domain)
        job.say(f"done: the site will appear at {address} within a few minutes")
        return {
            "pages_url": address,
            "repo_url": f"https://github.com/{account}/{repo}",
            "domain": domain,
        }
    finally:
        api.close()


def _remember_export(root: Path, target: str, base_url: str) -> None:
    """Add (or update) the site's GitHub Pages export target, and its address if it had none."""
    doc = read_site_toml(root)
    exports = doc.get("exports")
    if exports is None:
        exports = tomlkit.aot()
        doc["exports"] = exports
    for e in exports:
        if e.get("name") == EXPORT_NAME or e.get("target") == target:
            e["name"], e["target"], e["platform"], e["layout"] = EXPORT_NAME, target, "web", "plain"
            break
    else:
        t = tomlkit.table()
        t["name"], t["platform"], t["target"], t["layout"] = EXPORT_NAME, "web", target, "plain"
        exports.append(t)
    if base_url:
        host = base_url.split("//", 1)[1].strip("/")
        set_path(doc, "platforms.web.base_url", base_url.rstrip("/"))
        set_path(doc, "platforms.web.hostnames", [host])
    write_site_toml(root, doc)
