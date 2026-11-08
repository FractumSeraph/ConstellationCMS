"""``constellation server install``: directories, server.toml, the admin token, a systemd unit."""

from __future__ import annotations

import contextlib
import secrets
import sys
from pathlib import Path

from constellation.server.config import ServerConfig, save_server_config
from constellation.server.releases import hash_admin_token

UNIT = """[Unit]
Description=ConstellationCMS server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
ExecStart={exe} server run --config {config}
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={data}

[Install]
WantedBy=multi-user.target
"""


STUDIO_UNIT = """[Unit]
Description=ConstellationCMS studio (on the server)
After=network-online.target constellation.service

[Service]
Type=simple
User={user}
Group={user}
Environment=CONSTELLATION_STATE_DIR={data}/studio-state
Environment=CONSTELLATION_CACHE_DIR={data}/studio-cache
ExecStart={exe} studio --workspace {workspace} --bind 127.0.0.1 --port {port} --no-browser --state-dir {data}/studio-state
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def install(
    *,
    data_dir: Path,
    user: str,
    admin_hostname: str,
    tls_mode: str,
    exe: Path | None = None,
    unit_dir: Path | None = None,
    write_unit: bool = True,
    plain_port: int = 8080,
    studio_hostname: str = "",
    studio_workspace: Path | None = None,
    studio_port: int = 8787,
) -> tuple[Path, str]:
    """Create the data directory and server.toml, mint the admin token, write the unit file.

    Returns the config path and the one-time admin token (printed by the CLI, never stored raw).
    """
    data_dir = Path(data_dir)
    for sub in ("sites", "certs", "logs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    token = "cst_admin_" + secrets.token_urlsafe(32)
    cfg = ServerConfig(data_dir=str(data_dir))
    cfg.admin.hostname = admin_hostname.lower()
    cfg.admin.token_hash = hash_admin_token(token)
    cfg.https.tls_mode = tls_mode  # type: ignore[assignment]
    cfg.https.plain_port = plain_port
    if studio_hostname:
        cfg.studio.enabled = True
        cfg.studio.hostname = studio_hostname.lower()
        cfg.studio.upstream = f"http://127.0.0.1:{studio_port}"
    config_path = data_dir / "server.toml"
    save_server_config(config_path, cfg)
    with contextlib.suppress(OSError):
        config_path.chmod(0o600)
    if write_unit:
        exe = exe or Path(sys.argv[0]).resolve()
        unit_dir = unit_dir or Path("/etc/systemd/system")
        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            (unit_dir / "constellation.service").write_text(
                UNIT.format(user=user, exe=exe, config=config_path, data=data_dir), encoding="utf-8"
            )
        except OSError as e:
            print(f"warning: could not write the systemd unit: {e}", file=sys.stderr)
        if studio_hostname:
            workspace = studio_workspace or (data_dir / "workspace")
            workspace.mkdir(parents=True, exist_ok=True)
            try:
                (unit_dir / "constellation-studio.service").write_text(
                    STUDIO_UNIT.format(
                        user=user, exe=exe, data=data_dir, workspace=workspace, port=studio_port
                    ),
                    encoding="utf-8",
                )
            except OSError as e:
                print(f"warning: could not write the studio unit: {e}", file=sys.stderr)
    return config_path, token


def proxy_snippet(hostnames: list[str], plain_port: int, admin_hostname: str) -> str:
    """An nginx/openresty server block for behind-proxy mode."""
    names = " ".join(sorted(set(hostnames + [admin_hostname])))
    return f"""server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {names};
    # ssl_certificate / ssl_certificate_key as managed by your existing setup (certbot etc.)
    client_max_body_size 300m;
    location / {{
        proxy_pass http://127.0.0.1:{plain_port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }}
}}
"""
