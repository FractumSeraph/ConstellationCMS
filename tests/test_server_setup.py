"""Stage B: setting up a server from the studio, against a simulated machine.

A real run against a fresh Ubuntu 24.04 (a container with systemd and SSH) was done by hand
during development; these tests pin the decisions and the commands.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from constellation.studio import provision
from constellation.studio.provision import Check, Result

OS_RELEASE = 'PRETTY_NAME="Ubuntu 24.04.5 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n'
SS_FRESH = (
    'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
    'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=2,fd=3))\n'
)


def _inspect_output(
    release: str = OS_RELEASE,
    ports: str = SS_FRESH,
    caddy: str = "",
    ufw: str = "",
    installed: str = "",
) -> str:
    return (
        f"{release}##ARCH\nx86_64\n##MEM\n1987\n##DISK\n20000000\n##PORTS\n{ports}##CADDY\n{caddy}\n"
        f"##UFW\n{ufw}\n##INSTALLED\n{installed}\n##IP\n203.0.113.10\n"
    )


class FakeRemote:
    """Answers the commands the setup sends, and records them."""

    def __init__(self, inspect_out: str = "", fail: str = "") -> None:
        self.inspect_out = inspect_out or _inspect_output()
        self.fail = fail
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}

    def run(self, command: str, *, timeout: float = 600, stdin: str = "") -> Result:
        self.commands.append(command)
        if self.fail and self.fail in command:
            return Result(1, "", "E: Unable to locate package python3-venv")
        if "##ARCH" in command:
            return Result(0, self.inspect_out, "")
        if "server install" in command or "server new-key" in command:
            out = (
                "wrote /var/lib/constellation/server.toml\ninstallation key (shown once): ...\n  cst_admin_"
                + "A" * 43
                + "\n"
            )
            if "--with-studio" in command:
                out += "  https://studio.203-0-113-10.sslip.io/setup?code=ABCD-EFGH-JKMN\n"
            return Result(0, out, "")
        if "is-active constellation" in command:
            return Result(0, "active\n", "")
        return Result(0, "", "")

    def put(self, data: bytes, path: str, mode: int = 0o644) -> None:
        self.files[path] = data

    def close(self) -> None:
        pass


WHEEL = ("constellationcms-0.1.0-py3-none-any.whl", b"wheel")


# ---- looking -----------------------------------------------------------------------------------
def test_inspect_reads_the_system() -> None:
    c = provision.inspect(FakeRemote())
    assert (c.os_id, c.os_version, c.arch, c.memory_mb, c.disk_gb) == (
        "ubuntu",
        (24, 4),
        "x86_64",
        1987,
        19.1,
    )
    assert c.ports == {22: "sshd", 53: "systemd-resolve"} and not c.caddy and not c.ufw_active
    assert c.public_ip == "203.0.113.10" and not c.installed


def test_inspect_reads_netstat_and_caddy_and_ufw() -> None:
    netstat = (
        "tcp 0 0 0.0.0.0:443 0.0.0.0:* LISTEN 812/caddy\ntcp6 0 0 :::80 :::* LISTEN 812/caddy\n"
    )
    c = provision.inspect(
        FakeRemote(
            _inspect_output(ports=netstat, caddy="active", ufw="Status: active", installed="yes")
        )
    )
    assert c.ports == {443: "caddy", 80: "caddy"} and c.caddy and c.ufw_active and c.installed
    c = provision.inspect(FakeRemote(_inspect_output(ufw="Status: inactive")))
    assert not c.ufw_active


# ---- planning ----------------------------------------------------------------------------------
def _check(**kw: object) -> Check:
    c = provision.inspect(FakeRemote())
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_plan_for_a_fresh_server() -> None:
    plan = provision.make_plan(
        _check(), "203-0-113-10.sslip.io", studio_host="studio.203-0-113-10.sslip.io"
    )
    assert plan.ok and plan.mode == "acme"
    text = " ".join(plan.steps)
    assert "Let's Encrypt" in text and "studio" in text and "SSH key" in text
    assert any("no DNS setup" in n for n in plan.notes)


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"os_id": "centos", "os_name": "CentOS 7"}, "works on Ubuntu"),
        ({"os_version": (20, 4)}, "too old"),
        ({"memory_mb": 256}, "512 MB"),
        ({"disk_gb": 1.0}, "disk"),
        ({"ports": {80: "nginx", 22: "sshd"}}, "nginx on port 80"),
        ({"ports": {1965: "agate"}}, "Port 1965"),
        ({"arch": "armv7l"}, "processor"),
    ],
)
def test_plan_explains_why_it_cannot_continue(changes: dict, says: str) -> None:
    plan = provision.make_plan(_check(**changes), "203-0-113-10.sslip.io")
    assert not plan.ok and any(says in p for p in plan.problems), plan.problems


def test_plan_shares_a_server_with_caddy() -> None:
    plan = provision.make_plan(
        _check(ports={80: "caddy", 443: "caddy", 8180: "other"}, caddy=True), "cms.example.org"
    )
    assert plan.ok and plan.mode == "caddy" and plan.plain_port == 8181
    assert any("Caddy" in s for s in plan.steps)


def test_free_names() -> None:
    assert provision.auto_host("203.0.113.10") == "203-0-113-10.sslip.io"
    assert provision.auto_host("203.0.113.10", "studio") == "studio.203-0-113-10.sslip.io"
    assert provision.auto_host("2001:db8::1").endswith(".sslip.io")
    assert provision.auto_host("example.org") == ""


# ---- doing -------------------------------------------------------------------------------------
def test_install_on_a_fresh_server() -> None:
    remote = FakeRemote()
    check = provision.inspect(remote)
    plan = provision.make_plan(
        check, "203-0-113-10.sslip.io", studio_host="studio.203-0-113-10.sslip.io"
    )
    said: list[str] = []
    done = provision.install(remote, plan, check, WHEEL, said.append)
    assert done.install_key == "cst_admin_" + "A" * 43
    assert done.studio_link == "https://studio.203-0-113-10.sslip.io/setup?code=ABCD-EFGH-JKMN"
    assert done.admin_url == "https://203-0-113-10.sslip.io/_constellation"
    assert remote.files == {"/tmp/" + WHEEL[0]: b"wheel"}
    joined = "\n".join(remote.commands)
    assert "apt-get install -y -q python3-venv" in joined
    assert "--tls-mode acme" in joined and "--with-studio studio.203-0-113-10.sslip.io" in joined
    assert "systemctl enable constellation constellation-studio" in joined
    assert "ufw allow" not in joined  # the firewall is left alone when it isn't on
    assert said[0].startswith("Installing system packages")


def test_install_behind_caddy_with_a_firewall() -> None:
    remote = FakeRemote(
        _inspect_output(
            ports='LISTEN 0 4096 *:443 *:* users:(("caddy",pid=9,fd=7))\n',
            caddy="active",
            ufw="Status: active",
        )
    )
    check = provision.inspect(remote)
    plan = provision.make_plan(check, "cms.example.org", gopher=True)
    provision.install(remote, plan, check, WHEEL, lambda m: None)
    joined = "\n".join(remote.commands)
    assert "--tls-mode behind-proxy --plain-port 8180 --gopher" in joined
    assert (
        "proxy-snippet --caddy" in joined
        and "caddy validate" in joined
        and ".bak-constellation-" in joined
    )
    assert (
        "ufw allow 1965/tcp" in joined
        and "ufw allow 70/tcp" in joined
        and "ufw allow 80/tcp" not in joined
    )


def test_running_again_keeps_the_settings() -> None:
    remote = FakeRemote(
        _inspect_output(
            installed="yes",
            ports=SS_FRESH
            + 'LISTEN 0 4096 0.0.0.0:443 0.0.0.0:* users:(("constellation",pid=5,fd=6))\n',
        )
    )
    check = provision.inspect(remote)
    plan = provision.make_plan(check, "203-0-113-10.sslip.io")
    assert plan.ok, plan.problems
    done = provision.install(remote, plan, check, WHEEL, lambda m: None)
    assert done.install_key == "" and not any("server install" in c for c in remote.commands)


def test_a_failed_step_says_what_failed() -> None:
    remote = FakeRemote(fail="apt-get install")
    check = provision.inspect(remote)
    plan = provision.make_plan(check, "203-0-113-10.sslip.io")
    with pytest.raises(
        provision.ProvisionError, match="Installing system packages failed: E: Unable to locate"
    ):
        provision.install(remote, plan, check, WHEEL, lambda m: None)


def test_ssh_key(tmp_path: Path) -> None:
    public = provision.new_ssh_key(tmp_path / "k", "studio@pc")
    assert re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+ studio@pc", public)
    assert (tmp_path / "k").read_text().startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    remote = FakeRemote()
    provision.add_ssh_key(remote, public)
    assert "authorized_keys" in remote.commands[0] and "grep -qxF" in remote.commands[0]
    with pytest.raises(provision.ProvisionError):
        provision.add_ssh_key(remote, "ssh-rsa AAAA; rm -rf /")


def test_self_wheel_is_installable_shape() -> None:
    from constellation.studio.selfwheel import build_wheel

    name, data = build_wheel()
    assert re.fullmatch(r"constellationcms-[\w.]+-py3-none-any\.whl", name)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "constellation/cli.py" in names and "constellation/core/assets/features.js" in names
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        info = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        assert "constellation = constellation.cli:main" in zf.read(info).decode()
        record = zf.read(next(n for n in names if n.endswith("RECORD"))).decode()
        assert "constellation/cli.py,sha256=" in record


# ---- the whole job, and the wizard ------------------------------------------------------------
def test_setup_job_connects_and_retires_the_installation_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from constellation.studio import server_setup
    from constellation.studio import servers as srv
    from constellation.studio.jobs import Job
    from constellation.studio.state import State
    from constellation.studio.workspace import load_workspace

    remote = FakeRemote(_inspect_output())
    monkeypatch.setattr(provision, "SSHRemote", lambda *a, **k: remote)
    monkeypatch.setattr(server_setup, "_wait_for_https", lambda url, say, **k: None)
    connected: dict[str, str] = {}

    def fake_connect(state, server, secret, label):  # noqa: ANN001, ANN202
        connected["secret"] = secret
        state.kv_set(srv.admin_key_name(server.id), "cst_admin_dabcdefgh_" + "x" * 40)
        return "abcdefgh"

    removed: list[str] = []
    monkeypatch.setattr(srv, "connect_computer", fake_connect)
    from constellation.publish import client as pc

    monkeypatch.setattr(
        pc.PublishClient, "remove_device", lambda self, dev: removed.append(dev) or {"ok": True}
    )
    ws = tmp_path / "ws"
    (ws / "sites").mkdir(parents=True)
    state = State(tmp_path / "state", tmp_path / "cache")
    check = provision.inspect(remote)
    plan = provision.make_plan(
        check, "203-0-113-10.sslip.io", studio_host="studio.203-0-113-10.sslip.io"
    )
    job = Job(id="j", kind="server setup", site="_servers")
    result = server_setup.run_setup(
        job,
        state,
        ws,
        host="203.0.113.10",
        port=22,
        user="root",
        password="secret pass",
        name="My server",
        plan=plan,
        check=check,
        label="studio on laptop",
    )
    server = load_workspace(ws).servers[0]
    assert result == {
        "server_id": "my-server",
        "studio_link": "https://studio.203-0-113-10.sslip.io/setup?code=ABCD-EFGH-JKMN",
    }
    assert server.admin_url == "https://203-0-113-10.sslip.io/_constellation"
    assert connected["secret"] == "cst_admin_" + "A" * 43 and removed == ["install"]
    assert server_setup.ssh_key_path(state, "my-server").is_file()
    assert json.loads(state.kv_get("ssh:my-server"))["host"] == "203.0.113.10"
    assert any("ssh-ed25519" in c for c in remote.commands)
    assert state.kv_get("studio_setup_link:my-server").startswith("https://studio.")
    # The root password is never written anywhere in the studio's folder.
    for f in (tmp_path / "state").rglob("*"):
        if f.is_file():
            assert b"secret pass" not in f.read_bytes(), f
    assert not any("cst_admin_AAAA" in line for line in job.log)  # the key never shows


def test_wizard_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import shutil

    from fastapi.testclient import TestClient

    from constellation.studio import server_setup
    from constellation.studio.app import create_app
    from constellation.studio.state import State
    from constellation.studio.workspace import load_workspace, save_workspace

    remote = FakeRemote()
    monkeypatch.setattr(provision, "SSHRemote", lambda *a, **k: remote)
    started: dict[str, object] = {}
    monkeypatch.setattr(
        server_setup, "run_setup", lambda job, *a, **k: started.update(k) or {"server_id": "x"}
    )
    ws = tmp_path / "ws"
    (ws / "sites").mkdir(parents=True)
    save_workspace(load_workspace(ws))
    state = State(tmp_path / "state", tmp_path / "cache")
    with TestClient(
        create_app(state, ws), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as c:
        c.post(
            "/setup", data={"password": "correct horse battery", "confirm": "correct horse battery"}
        )
        csrf = re.search(r'name="csrf" content="([^"]+)"', c.get("/").text).group(1)
        assert 'href="/servers/setup"' in c.get("/servers").text
        page = c.get("/servers/setup").text
        assert "Root password" in page and "Ubuntu 24.04" in page
        r = c.post(
            "/servers/setup/check",
            data={"csrf": csrf, "host": "203.0.113.10", "password": "secret pass"},
        )
        assert "Ubuntu 24.04.5 LTS" in r.text and 'value="203-0-113-10.sslip.io"' in r.text
        assert "studio.203-0-113-10.sslip.io" in r.text and "Set it up" in r.text
        draft = re.search(r'name="draft" value="([^"]+)"', r.text).group(1)
        bad = c.post("/servers/setup/check", data={"csrf": csrf, "host": "bad host; rm -rf /"})
        assert "look like an IP address" in bad.text
        r = c.post(
            "/servers/setup/run",
            data={
                "csrf": csrf,
                "draft": draft,
                "name": "Mine",
                "admin_host": "203-0-113-10.sslip.io",
                "studio": "1",
            },
        )
        assert "Setting up" in r.text and 'data-job="' in r.text
        r = c.post(
            "/servers/setup/run",
            data={"csrf": csrf, "draft": draft, "name": "Mine", "admin_host": "x.sslip.io"},
        )
        assert "expired" in r.text  # a draft is used once
    import time

    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.05)
    assert started["password"] == "secret pass" and started["host"] == "203.0.113.10"
    assert started["plan"].studio_host == "studio.203-0-113-10.sslip.io"
    for f in (tmp_path / "state").rglob("*"):
        if f.is_file():
            assert b"secret pass" not in f.read_bytes(), f
    shutil.rmtree(tmp_path / "ws")
