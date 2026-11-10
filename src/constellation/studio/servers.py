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


def admin_key_name(server_id: str) -> str:
    return f"admin:{server_id}"


def admin_key(state: State, server_id: str) -> str:
    """This computer's own credential for a server ("" when not connected). Never synced."""
    return str(state.kv_get(admin_key_name(server_id), "") or "")


def connect_computer(state: State, server: ServerEntry, secret: str, label: str) -> str:
    """Connect this computer to a server with a pairing code or the installation key.

    Either way this computer ends up with a credential of its own, which it keeps locally; the
    installation key itself is not stored. Returns the new credential's id.
    """
    secret = secret.strip()
    if not secret:
        raise RuntimeError("enter the pairing code shown on a computer that is already connected")
    if secret.startswith("cst_admin_"):
        client = PublishClient(server.admin_url, secret)
        try:
            made = client.add_device(label)
        finally:
            client.close()
    else:
        client = PublishClient(server.admin_url, "")
        try:
            made = client.pair(secret, label)
        finally:
            client.close()
    state.kv_set(admin_key_name(server.id), str(made["key"]))
    return str(made["id"])


def disconnect_computer(state: State, server_id: str) -> None:
    state.kv_set(admin_key_name(server_id), "")


def _admin_client(state: State, server: ServerEntry) -> PublishClient:
    key = admin_key(state, server.id)
    if not key:
        raise RuntimeError(f"this computer is not connected to {server.name} yet")
    return PublishClient(server.admin_url, key)


def pairing_code(state: State, server: ServerEntry) -> dict[str, Any]:
    client = _admin_client(state, server)
    try:
        return client.new_pairing()
    finally:
        client.close()


def computers(state: State, server: ServerEntry) -> dict[str, Any]:
    client = _admin_client(state, server)
    try:
        return client.devices()
    finally:
        client.close()


def remove_computer(state: State, server: ServerEntry, device_id: str) -> None:
    client = _admin_client(state, server)
    try:
        client.remove_device(device_id)
    finally:
        client.close()


def ensure_site_token(
    state: State, ws: Workspace, server: ServerEntry, site_id: str, label: str
) -> str:
    """The site's publish key on this computer, minted with this computer's credential if needed."""
    token = str(state.kv_get(token_key(server.id, site_id), "") or "")
    if token:
        return token
    key = admin_key(state, server.id)
    if not key:
        raise RuntimeError(
            f"this computer is not connected to {server.name}; connect it on the Servers page"
        )
    connect_site(state, ws, server, site_id, key, label)
    return str(state.kv_get(token_key(server.id, site_id), "") or "")


def import_bootstrap(state: State, ws: Workspace) -> str:
    """Connect a studio on a server to its own server, from the file the installer leaves.

    ``server install --with-studio`` writes ``server-connection.json`` (address and a credential
    made for this studio) into the studio's state folder; it is read once and deleted.
    """
    import json

    path = state.dir / "server-connection.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        url, key = str(data["admin_url"]), str(data["key"])
        name = str(data.get("name") or "This server")
    except (OSError, ValueError, KeyError):
        return ""
    server = next((s for s in ws.servers if s.admin_url.rstrip("/") == url.rstrip("/")), None)
    if server is None:
        sid = "this-server"
        taken = {s.id for s in ws.servers}
        n = 2
        while sid in taken:
            sid, n = f"this-server-{n}", n + 1
        server = ServerEntry(id=sid, name=name, admin_url=url)
        ws.servers.append(server)
        save_workspace(ws)
    state.kv_set(admin_key_name(server.id), key)
    path.unlink(missing_ok=True)
    return server.id


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
    if not token and admin_key(state, server.id) and root.parent.name == "sites":
        # A connected computer gets the site's publish key by itself the first time.
        from constellation.studio.locks import machine_label
        from constellation.studio.workspace import load_workspace

        job.say(f"getting a publish key for {site_id} from {server.name}")
        token = ensure_site_token(
            state,
            load_workspace(root.parent.parent),
            server,
            site_id,
            f"studio on {machine_label()}",
        )
    if not token:
        raise RuntimeError(
            f"this computer is not connected to {server.name}; connect it on the Servers page"
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
            planned: dict[str, Any] = {"planned": True, **{k: len(v) for k, v in diff.items()}}
            _run_linked_exports(job, root, site_id, site, apply=False)
            return planned
        report = client.publish(site_id, manifest, blob_reader(build), progress=job.say)
    finally:
        client.close()
    try:
        _run_linked_exports(job, root, site_id, site, apply=True)
    finally:
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


def _run_linked_exports(job: Job, root: Path, site_id: str, site: Any, *, apply: bool) -> None:
    """Run the export targets marked ``with_publish`` (such as GitHub Pages) after the server.

    Every target is attempted; if any fails, the job fails afterwards with all their errors,
    so a GitHub problem is never hidden behind a successful server publish.
    """
    from constellation.studio.publish_ops import run_export

    failures: list[str] = []
    for spec in site.config.exports:
        if not spec.with_publish:
            continue
        job.say(f"export {spec.name}: {spec.target}")
        try:
            run_export(job, root, site_id, spec.model_dump(), apply=apply)
        except Exception as e:  # noqa: BLE001 - reported to the user below
            job.say(f"export {spec.name} failed: {e}")
            failures.append(f"{spec.name}: {e}")
    if failures:
        raise RuntimeError(
            ("published to the server, but " if apply else "")
            + "these export targets failed: "
            + "; ".join(failures)
        )


def rollback(state: State, site_id: str, server: ServerEntry, release_id: str) -> dict[str, Any]:
    token = state.kv_get(token_key(server.id, site_id))
    if not token:
        raise RuntimeError("no publish token for this site on that server")
    client = PublishClient(server.admin_url, str(token))
    try:
        return client.activate(site_id, release_id)
    finally:
        client.close()


def analytics(state: State, site_id: str, server: ServerEntry, days: int) -> dict[str, Any]:
    token = state.kv_get(token_key(server.id, site_id))
    if not token:
        return {"error": "not connected"}
    client = PublishClient(server.admin_url, str(token))
    try:
        return {
            "overview": client.analytics(site_id, "overview", days),
            "content": client.analytics(site_id, "content", days).get("items", []),
            "technical": client.analytics(site_id, "technical", days),
            "referrers": client.analytics(site_id, "referrers", days).get("items", []),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
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
