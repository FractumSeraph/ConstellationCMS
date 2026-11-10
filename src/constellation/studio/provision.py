"""Turn a fresh VPS into a ConstellationCMS server from the studio, over SSH.

The person gives the server's address and its root password once. The studio connects, looks
at the system (:func:`inspect`), explains what it will do (:func:`make_plan`), and then does it
(:func:`install`): system packages, ConstellationCMS itself (uploaded from the studio's own copy),
the service, HTTPS, firewall rules, optionally a studio on the server, and an SSH key of the
studio's own so the root password is never kept. Every step is idempotent, so running the setup
again after a failure picks up where it stopped.

The work happens through :class:`Remote`, so tests drive it with a fake machine and the studio
with :class:`SSHRemote`.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

SUPPORTED = {"ubuntu": (22, 4), "debian": (11, 0)}
MIN_MEMORY_MB = 450
MIN_DISK_GB = 2
VENV = "/opt/constellation"
DATA = "/var/lib/constellation"


class ProvisionError(Exception):
    """A step failed; the message says what happened and what to do, in plain words."""


@dataclass(slots=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class Remote(Protocol):
    def run(self, command: str, *, timeout: float = 600, stdin: str = "") -> Result: ...
    def put(self, data: bytes, path: str, mode: int = 0o644) -> None: ...
    def close(self) -> None: ...


# ---- SSH ------------------------------------------------------------------------------------
class SSHRemote:
    """A server reached over SSH with a password or a key.

    The server's host key is remembered in ``known_hosts`` (in the studio's state folder) the
    first time, and a different key later is refused: that would mean someone in between.
    Commands run as root; a non-root user needs sudo (the password is used for it when given).
    """

    def __init__(
        self,
        host: str,
        *,
        user: str = "root",
        port: int = 22,
        password: str = "",
        key_file: Path | None = None,
        known_hosts: Path,
        timeout: float = 20,
    ) -> None:
        import paramiko

        self.user = user
        self.password = password
        self.client = paramiko.SSHClient()
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        known_hosts.touch(exist_ok=True)
        self.client.load_host_keys(str(known_hosts))
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(
                host,
                port=port,
                username=user,
                password=password or None,
                key_filename=str(key_file) if key_file else None,
                allow_agent=not password and key_file is None,
                look_for_keys=not password and key_file is None,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
            )
        except paramiko.BadHostKeyException as e:
            raise ProvisionError(
                "The server's identity changed since the studio last connected. If the server was "
                "reinstalled, remove it from the studio and set it up again; otherwise someone may "
                "be intercepting the connection."
            ) from e
        except paramiko.AuthenticationException as e:
            raise ProvisionError(
                "The server refused the password. Check it in your VPS provider's control panel "
                "(some send it by email) and try again."
            ) from e
        except (OSError, paramiko.SSHException) as e:
            raise ProvisionError(
                f"Could not reach {host} on port {port}: {e}. Check the address, and that the "
                "server is switched on."
            ) from e
        self.client.save_host_keys(str(known_hosts))

    def run(self, command: str, *, timeout: float = 600, stdin: str = "") -> Result:
        if self.user != "root":
            command = f"sudo -S -p '' sh -c {shlex.quote(command)}"
            stdin = (self.password + "\n" if self.password else "") + stdin
        chan_in, chan_out, chan_err = self.client.exec_command(command, timeout=timeout)
        if stdin:
            chan_in.write(stdin)
            chan_in.flush()
        chan_in.channel.shutdown_write()
        out = chan_out.read().decode("utf-8", "replace")
        err = chan_err.read().decode("utf-8", "replace")
        return Result(chan_out.channel.recv_exit_status(), out, err)

    def put(self, data: bytes, path: str, mode: int = 0o644) -> None:
        import io

        tmp = f"/tmp/constellation-upload-{int(time.time() * 1000)}"
        sftp = self.client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(data), tmp)
            sftp.chmod(tmp, mode)
        finally:
            sftp.close()
        r = self.run(f"mv {shlex.quote(tmp)} {shlex.quote(path)}")
        if not r.ok:
            raise ProvisionError(f"could not place {path}: {r.err.strip()}")

    def close(self) -> None:
        self.client.close()


# ---- looking at the server ----------------------------------------------------------------------
@dataclass(slots=True)
class Check:
    os_id: str = ""
    os_name: str = ""
    os_version: tuple[int, int] = (0, 0)
    arch: str = ""
    memory_mb: int = 0
    disk_gb: float = 0.0
    ports: dict[int, str] = field(default_factory=dict)  # port -> program listening
    caddy: bool = False
    ufw_active: bool = False
    installed: bool = False  # ConstellationCMS is already set up here
    public_ip: str = ""


def _version(text: str) -> tuple[int, int]:
    m = re.match(r"(\d+)(?:\.(\d+))?", text.strip().strip('"'))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def inspect(remote: Remote) -> Check:
    """Gather what the plan depends on, in one round trip."""
    script = (
        "cat /etc/os-release; echo '##ARCH'; uname -m; echo '##MEM'; "
        "awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo; echo '##DISK'; "
        "df -Pk / | awk 'NR==2 {print $4}'; echo '##PORTS'; "
        "(ss -Htlnp 2>/dev/null || netstat -tlnp 2>/dev/null); echo '##CADDY'; "
        "(command -v caddy >/dev/null && systemctl is-active caddy 2>/dev/null) || true; echo '##UFW'; "
        "(command -v ufw >/dev/null && ufw status 2>/dev/null | head -1) || true; echo '##INSTALLED'; "
        f"test -f {DATA}/server.toml && echo yes || true; echo '##IP'; "
        "(hostname -I 2>/dev/null | awk '{print $1}') || true"
    )
    r = remote.run(script, timeout=60)
    if not r.ok and "##ARCH" not in r.out:
        raise ProvisionError(f"could not look at the server: {r.err.strip() or r.out.strip()}")
    sections: dict[str, str] = {}
    current = "os"
    for line in r.out.splitlines():
        if line.startswith("##"):
            current = line[2:].strip().lower()
            sections[current] = ""
        else:
            sections[current] = sections.get(current, "") + line + "\n"
    c = Check()
    release = dict(
        line.split("=", 1) for line in sections.get("os", "").splitlines() if "=" in line
    )
    c.os_id = release.get("ID", "").strip('"').lower()
    c.os_name = release.get("PRETTY_NAME", c.os_id).strip('"')
    c.os_version = _version(release.get("VERSION_ID", "0"))
    c.arch = sections.get("arch", "").strip()
    c.memory_mb = int((sections.get("mem", "0").strip() or "0").split()[0])
    disk_kb = int((sections.get("disk", "0").strip() or "0").split()[0])
    c.disk_gb = round(disk_kb / 1024 / 1024, 1)
    for line in sections.get("ports", "").splitlines():
        parts = line.split()
        # ss -Htlnp: State Recv-Q Send-Q Local Peer Process; netstat -tlnp: Proto Recv Send Local ...
        if len(parts) < 4 or not re.search(r":\d+$", parts[3]):
            continue
        port = int(parts[3].rsplit(":", 1)[1])
        users = re.search(r'users:\(\("([^"]+)"', line)
        if users:
            prog = users.group(1)
        elif "/" in parts[-1]:
            prog = parts[-1].split("/", 1)[1]
        else:
            prog = "?"
        c.ports.setdefault(port, prog)
    c.caddy = sections.get("caddy", "").strip() == "active"
    c.ufw_active = (
        "active" in sections.get("ufw", "").lower()
        and "inactive" not in sections.get("ufw", "").lower()
    )
    c.installed = sections.get("installed", "").strip() == "yes"
    c.public_ip = sections.get("ip", "").strip()
    return c


# ---- the plan ------------------------------------------------------------------------------------
@dataclass(slots=True)
class Plan:
    mode: str  # "acme": the server answers ports 80 and 443 itself; "caddy": behind Caddy
    admin_host: str
    studio_host: str = ""
    gopher: bool = False
    plain_port: int = 8180
    steps: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # stop the setup
    notes: list[str] = field(default_factory=list)  # worth knowing, not blocking

    @property
    def ok(self) -> bool:
        return not self.problems


def auto_host(ip: str, prefix: str = "") -> str:
    """A name that points at ``ip`` with no DNS setup, through the free sslip.io service."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version == 6:
        label = str(addr.exploded).replace(":", "-")
    else:
        label = str(addr).replace(".", "-")
    return f"{prefix}.{label}.sslip.io" if prefix else f"{label}.sslip.io"


def make_plan(
    check: Check, admin_host: str, *, studio_host: str = "", gopher: bool = False
) -> Plan:
    """What the setup will do on this server, or why it can't, in plain words."""
    caddy = check.caddy or check.ports.get(443, "") == "caddy"
    plan = Plan(
        mode="caddy" if caddy else "acme",
        admin_host=admin_host.lower(),
        studio_host=studio_host.lower(),
        gopher=gopher,
    )
    need = SUPPORTED.get(check.os_id)
    if need is None:
        plan.problems.append(
            f"This server runs {check.os_name or 'an unknown system'}. The setup works on Ubuntu "
            "(22.04 or newer) and Debian (11 or newer); most VPS providers let you pick Ubuntu "
            "when you create or rebuild the server."
        )
    elif check.os_version < need:
        plan.problems.append(
            f"{check.os_name} is too old; rebuild the server with Ubuntu 24.04 if you can."
        )
    if check.arch not in ("x86_64", "aarch64", "amd64", "arm64"):
        plan.problems.append(
            f"Unusual processor type ({check.arch}); the setup supports x86-64 and ARM64."
        )
    if check.memory_mb and check.memory_mb < MIN_MEMORY_MB:
        plan.problems.append(
            f"The server has {check.memory_mb} MB of memory; at least 512 MB is needed."
        )
    if check.disk_gb and check.disk_gb < MIN_DISK_GB:
        plan.problems.append(
            f"Only {check.disk_gb} GB of disk is free; at least {MIN_DISK_GB} GB is needed."
        )
    if not plan.admin_host or not re.fullmatch(
        r"[a-z0-9.-]+\.[a-z]{2,}|[a-z0-9.-]+\.sslip\.io", plan.admin_host
    ):
        plan.problems.append("The server needs an address (a name, not just an IP address).")
    busy = {
        p: prog
        for p, prog in check.ports.items()
        if p in (80, 443) and prog not in ("caddy", "constellation")
    }
    if plan.mode == "acme" and busy:
        names = ", ".join(f"{prog} on port {p}" for p, prog in sorted(busy.items()))
        plan.problems.append(
            f"Another program already answers web requests ({names}). The setup can share a server "
            "with Caddy, but not with this one; use a fresh server, or ask for help."
        )
    for port in (1965,) + ((70,) if gopher else ()):
        prog = check.ports.get(port)
        if prog and prog != "constellation":
            plan.problems.append(f"Port {port} is already used by {prog}.")
    if plan.mode == "caddy":
        while plan.plain_port in check.ports:
            plan.plain_port += 1
    if check.installed:
        plan.notes.append(
            "ConstellationCMS is already set up here; the setup will update it and connect this "
            "studio, keeping its sites and settings."
        )

    steps = [
        "Install the system packages it needs (Python's virtual environments)",
        f"Install ConstellationCMS in {VENV}",
    ]
    if not check.installed:
        steps.append(f"Create the server's settings, with {plan.admin_host} as its address")
    if plan.mode == "acme":
        steps.append("Answer web requests itself, with free HTTPS certificates from Let's Encrypt")
    else:
        steps.append(f"Add {plan.admin_host} to the existing Caddy (with a backup of its settings)")
    if check.ufw_active:
        ports = (
            (["80", "443"] if plan.mode == "acme" else []) + ["1965"] + (["70"] if gopher else [])
        )
        steps.append(f"Open ports {', '.join(ports)} in the firewall")
    steps.append("Start the service, and start it again whenever the server restarts")
    if plan.studio_host:
        steps.append(f"Add a studio you can open from any browser at https://{plan.studio_host}")
    steps += [
        "Connect this studio to the server",
        "Give this studio its own SSH key, so your root password is not kept anywhere",
    ]
    plan.steps = steps
    if plan.admin_host.endswith(".sslip.io"):
        plan.notes.append(
            f"{plan.admin_host} is a free name that always points at this server, so no DNS setup "
            "is needed for it. Your sites will use your own domain."
        )
    return plan


# ---- doing it ---------------------------------------------------------------------------------
INSTALL_KEY = re.compile(r"\b(cst_admin_[A-Za-z0-9_-]{20,})\b")
SETUP_LINK = re.compile(r"(https://\S+/setup\?code=[A-Z0-9-]+)")


@dataclass(slots=True)
class Installed:
    install_key: str = ""  # only when the server was created now
    studio_link: str = ""
    admin_url: str = ""


def _must(remote: Remote, command: str, what: str, *, timeout: float = 900) -> Result:
    r = remote.run(command, timeout=timeout)
    if not r.ok:
        detail = (r.err.strip() or r.out.strip()).splitlines()[-3:]
        raise ProvisionError(f"{what} failed: {' / '.join(detail) or f'exit code {r.code}'}")
    return r


def install(
    remote: Remote,
    plan: Plan,
    check: Check,
    wheel: tuple[str, bytes],
    say: Callable[[str], None],
) -> Installed:
    """Carry out ``plan``. Safe to run again: finished steps are skipped or repeated harmlessly."""
    if not plan.ok:
        raise ProvisionError("; ".join(plan.problems))
    done = Installed(admin_url=f"https://{plan.admin_host}/_constellation")
    say("Installing system packages (this can take a few minutes)…")
    _must(
        remote,
        "export DEBIAN_FRONTEND=noninteractive; apt-get update -q >/dev/null && "
        "apt-get install -y -q python3-venv python3-pip curl ca-certificates >/dev/null",
        "Installing system packages",
    )
    say("Installing ConstellationCMS…")
    name, data = wheel
    remote.put(data, f"/tmp/{name}")
    _must(
        remote,
        f"test -x {VENV}/bin/python || python3 -m venv {VENV}; "
        f"{VENV}/bin/pip install -q --upgrade pip >/dev/null; "
        f"{VENV}/bin/pip install -q --force-reinstall /tmp/{shlex.quote(name)}",
        "Installing ConstellationCMS",
    )
    if not check.installed:
        say(f"Creating the server's settings for {plan.admin_host}…")
        args = [
            f"{VENV}/bin/constellation",
            "server",
            "install",
            "--admin-hostname",
            plan.admin_host,
            "--tls-mode",
            "acme" if plan.mode == "acme" else "behind-proxy",
        ]
        if plan.mode == "caddy":
            args += ["--plain-port", str(plan.plain_port)]
        if plan.gopher:
            args.append("--gopher")
        if plan.studio_host:
            args += ["--with-studio", plan.studio_host]
        r = _must(remote, shlex.join(args), "Creating the server's settings")
        key = INSTALL_KEY.search(r.out)
        if not key:
            raise ProvisionError("the server was set up but did not print its installation key")
        done.install_key = key.group(1)
        link = SETUP_LINK.search(r.out)
        done.studio_link = link.group(1) if link else ""
    if plan.mode == "caddy":
        say("Adding the server to Caddy…")
        names = shlex.join(
            [
                f"{VENV}/bin/constellation",
                "server",
                "proxy-snippet",
                "--caddy",
                "--config",
                f"{DATA}/server.toml",
            ]
        )
        _must(
            remote,
            "set -e; f=/etc/caddy/Caddyfile; "
            f"grep -q {shlex.quote(plan.admin_host)} $f || {{ cp $f $f.bak-constellation-$(date +%Y%m%d%H%M%S); "
            f"echo >> $f; {names} >> $f; }}; caddy validate --config $f --adapter caddyfile >/dev/null; "
            "systemctl reload caddy",
            "Adding the server to Caddy",
        )
    if check.ufw_active:
        ports = (["80/tcp", "443/tcp"] if plan.mode == "acme" else []) + ["1965/tcp"]
        if plan.gopher:
            ports.append("70/tcp")
        say("Opening the firewall…")
        _must(
            remote,
            "; ".join(f"ufw allow {p} comment ConstellationCMS >/dev/null" for p in ports),
            "Opening the firewall",
        )
    say("Starting the service…")
    services = "constellation" + (" constellation-studio" if plan.studio_host else "")
    _must(
        remote,
        f"systemctl daemon-reload && systemctl enable {services} >/dev/null 2>&1; systemctl restart {services}",
        "Starting the service",
    )
    r = _must(remote, "sleep 2; systemctl is-active constellation", "Checking the service")
    if r.out.strip() != "active":
        raise ProvisionError(
            "the service did not start; the studio can show its log under Servers → More"
        )
    return done


def add_ssh_key(remote: Remote, public_key: str) -> None:
    """Let the studio log in with its own key from now on (appended once, never duplicated)."""
    key = public_key.strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+( [^\n]*)?", key):
        raise ProvisionError("bad public key")
    _must(
        remote,
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qxF {shlex.quote(key)} ~/.ssh/authorized_keys || echo {shlex.quote(key)} >> ~/.ssh/authorized_keys",
        "Adding the studio's SSH key",
    )


def new_ssh_key(path: Path, comment: str) -> str:
    """Create an Ed25519 key pair at ``path`` (private, owner-only) and return the public line."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(private)
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    )
    line = f"{public.decode()} {comment}".strip()
    path.with_suffix(".pub").write_text(line + "\n", encoding="utf-8")
    return line
