"""Server registry operations for the studio: connecting, token storage (machine-local), publishing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.client import PublishClient, blob_reader, release_manifest
from constellation.studio.jobs import Job
from constellation.studio.publish_ops import _record_release
from constellation.studio.state import State
from constellation.studio.workspace import ServerEntry, Workspace, save_workspace


def token_key(server_id: str, site_id: str) -> str:
    return f"token:{server_id}:{site_id}"


def connect_site(
    state: State, ws: Workspace, server: ServerEntry, site_id: str, admin_token: str, label: str
) -> str:
    """Mint a per-site publish token with the admin token; store only the site token, locally."""
    client = PublishClient(server.admin_url, admin_token)
    try:
        minted = client.mint_token(site_id, label)
    finally:
        client.close()
    state.kv_set(token_key(server.id, site_id), minted["token"])
    entry = ws.site(site_id)
    if entry is not None and entry.server != server.id:
        entry.server = server.id
        save_workspace(ws)
    return str(minted["id"])


def has_token(state: State, server_id: str, site_id: str) -> bool:
    return bool(state.kv_get(token_key(server_id, site_id)))


def forget_token(state: State, server_id: str, site_id: str) -> None:
    state.kv_set(token_key(server_id, site_id), "")


def server_status(server: ServerEntry, admin_token: str) -> dict[str, Any]:
    client = PublishClient(server.admin_url, admin_token)
    try:
        return client.status()
    finally:
        client.close()


def run_publish(
    job: Job, state: State, root: Path, site_id: str, server: ServerEntry, *, apply: bool
) -> dict[str, Any]:
    token = state.kv_get(token_key(server.id, site_id))
    if not token:
        raise RuntimeError(
            f"no publish token for {site_id} on {server.name}; connect the site first"
        )
    job.say(f"loading {site_id}")
    site = load_site(root, site_id=site_id)
    now = datetime.now(UTC)
    job.say("building every enabled platform")
    build = build_site(site, now=now)
    errors = [w for pb in build.platforms.values() for w in pb.warnings if w.severity == "error"]
    if errors:
        for w in errors:
            job.say(f"error {w.code}: {w.message}")
        raise RuntimeError(f"{len(errors)} error-level warnings; fix them before publishing")
    manifest = release_manifest(build, site)
    client = PublishClient(server.admin_url, str(token))
    try:
        diff, missing = client.plan(site_id, manifest)
        job.say(
            f"{server.name}: {len(diff['added'])} added, {len(diff['changed'])} changed, {len(diff['removed'])} removed; {len(missing)} blobs to upload"
        )
        for p in diff["added"]:
            job.say(f"+ {p}")
        for p in diff["changed"]:
            job.say(f"~ {p}")
        for p in diff["removed"]:
            job.say(f"- {p}")
        if not apply:
            return {"planned": True, **{k: len(v) for k, v in diff.items()}}
        report = client.publish(site_id, manifest, blob_reader(build), progress=job.say)
    finally:
        client.close()
    _record_release(
        root,
        {
            "time": now.isoformat(),
            "kind": "publish",
            "name": server.name,
            "platform": ",".join(build.platforms),
            "release": report.release_id,
            "target": server.admin_url,
        },
    )
    return {
        "release": report.release_id,
        "uploaded": report.uploaded,
        "skipped": report.skipped,
        "activated": report.activated,
    }


def rollback(state: State, site_id: str, server: ServerEntry, release_id: str) -> dict[str, Any]:
    token = state.kv_get(token_key(server.id, site_id))
    if not token:
        raise RuntimeError("no publish token for this site on that server")
    client = PublishClient(server.admin_url, str(token))
    try:
        return client.activate(site_id, release_id)
    finally:
        client.close()


def releases(state: State, site_id: str, server: ServerEntry) -> dict[str, Any]:
    token = state.kv_get(token_key(server.id, site_id))
    if not token:
        return {"releases": [], "current": None, "error": "not connected"}
    client = PublishClient(server.admin_url, str(token))
    try:
        return client.releases(site_id)
    except Exception as e:  # noqa: BLE001
        return {"releases": [], "current": None, "error": str(e)}
    finally:
        client.close()
