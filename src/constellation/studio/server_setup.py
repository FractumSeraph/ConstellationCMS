"""The studio's "Set up a server" job: provision over SSH, then connect, with no tokens shown.

Runs :func:`constellation.studio.provision.install`, adds the server to the sites folder, gives
the studio an SSH key of its own on the server (so the root password typed into the wizard is
used once and never stored), waits for HTTPS, connects this computer with the installation key
the server printed, and then retires that key: nobody saw it, so nobody needs it. If every
computer is ever lost, the studio's SSH key can make a new one (``server new-key``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from constellation.studio import provision
from constellation.studio import servers as srv
from constellation.studio.jobs import Job
from constellation.studio.state import State
from constellation.studio.workspace import ServerEntry, load_workspace, save_workspace

HTTPS_WAIT_SECONDS = 240


def ssh_key_path(state: State, server_id: str) -> Path:
    return state.dir / "ssh" / f"{server_id}-ed25519"


def _wait_for_https(url: str, say: Any, *, seconds: float = HTTPS_WAIT_SECONDS) -> None:
    say("Waiting for the HTTPS certificate (usually under a minute)…")
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url + "/healthz", timeout=10)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(5)
    raise provision.ProvisionError(
        f"the server is running but its HTTPS address isn't answering yet ({last}). This usually "
        "fixes itself within a few minutes: run the setup again to finish connecting."
    )


def run_setup(
    job: Job,
    state: State,
    workspace_root: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    name: str,
    plan: provision.Plan,
    check: provision.Check,
    label: str,
) -> dict[str, Any]:
    from slugify import slugify

    from constellation.publish.client import PublishClient
    from constellation.studio.selfwheel import build_wheel

    say = job.say
    say(f"Connecting to {host}…")
    remote = provision.SSHRemote(
        host, user=user, port=port, password=password, known_hosts=state.dir / "known_hosts"
    )
    try:
        done = provision.install(remote, plan, check, build_wheel(), say)
        ws = load_workspace(workspace_root)
        server = next((s for s in ws.servers if s.admin_url == done.admin_url), None)
        if server is None:
            base = slugify(name or plan.admin_host)[:40] or "server"
            sid, n = base, 2
            while ws.server(sid) is not None:
                sid, n = f"{base}-{n}", n + 1
            server = ServerEntry(sid, name or plan.admin_host, done.admin_url)
            ws.servers.append(server)
            save_workspace(ws)
        say("Giving this studio its own SSH key for the server…")
        key_path = ssh_key_path(state, server.id)
        if key_path.is_file() and key_path.with_suffix(".pub").is_file():
            public = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
        else:
            public = provision.new_ssh_key(key_path, f"constellation-studio@{label}")
        provision.add_ssh_key(remote, public)
        state.kv_set(
            f"ssh:{server.id}",
            json.dumps({"host": host, "port": port, "user": user, "key": str(key_path)}),
        )
        install_key = done.install_key
        if not install_key and not srv.admin_key(state, server.id):
            # Already set up by someone else: make a fresh installation key to connect with.
            say("Making a new installation key to connect with…")
            r = provision._must(
                remote,
                f"{provision.VENV}/bin/constellation server new-key --config {provision.DATA}/server.toml"
                " && systemctl restart constellation",
                "Making a new installation key",
            )
            found = provision.INSTALL_KEY.search(r.out)
            install_key = found.group(1) if found else ""
    finally:
        remote.close()
    _wait_for_https(done.admin_url, say)
    if not srv.admin_key(state, server.id):
        if not install_key:
            raise provision.ProvisionError("could not get a key to connect this studio")
        say("Connecting this studio to the server…")
        srv.connect_computer(state, server, install_key, label)
        client = PublishClient(server.admin_url, srv.admin_key(state, server.id))
        try:
            client.remove_device("install")  # nobody saw it; this studio has its own now
        finally:
            client.close()
    if done.studio_link:
        state.kv_set(f"studio_setup_link:{server.id}", done.studio_link)
    say(f"All set: {server.name} is ready.")
    return {"server_id": server.id, "studio_link": done.studio_link}
