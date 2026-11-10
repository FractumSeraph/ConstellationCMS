"""server.toml: listeners, TLS mode, admin settings, limits."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Literal

import tomlkit
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpConfig(Strict):
    enabled: bool = True
    bind: list[str] = Field(default_factory=lambda: ["0.0.0.0"])
    port: int = 80


class HttpsConfig(Strict):
    enabled: bool = True
    port: int = 443
    tls_mode: Literal["acme", "external", "behind-proxy"] = "behind-proxy"
    acme_email: str = ""
    acme_directory: str = "https://acme-v02.api.letsencrypt.org/directory"
    cert_dir: str = ""
    plain_port: int = 8080
    plain_bind: str = "127.0.0.1"


class GeminiConfig(Strict):
    enabled: bool = True
    bind: list[str] = Field(default_factory=lambda: ["0.0.0.0"])
    port: int = 1965
    cert_years: int = 5


class GopherConfig(Strict):
    enabled: bool = False
    bind: list[str] = Field(default_factory=list)  # empty: the same addresses as Gemini
    port: int = 70
    site: str = ""  # the one site this listener serves (Gopher has no virtual hosting); empty = the first with a gopher tree


class AdminConfig(Strict):
    hostname: str = ""
    token_hash: str = ""


class StudioProxyConfig(Strict):
    """A studio instance on this VPS, reachable through the server's HTTPS at its own hostname."""

    enabled: bool = False
    hostname: str = ""
    upstream: str = "http://127.0.0.1:8787"
    # The studio's state folder, so the server can hand its setup link to a desktop studio that
    # connects with the admin token. Empty when the studio runs as another user.
    state_dir: str = ""


class Limits(Strict):
    max_connections: int = 512
    request_timeout_s: int = 10
    gemini_handshake_timeout_s: int = 10
    max_release_bytes: int = 2 * 1024 * 1024 * 1024
    keep_releases: int = 10
    rate_limit_per_minute: int = 600


class AnalyticsServerConfig(Strict):
    enabled: bool = True
    ip_policy: Literal["raw", "truncate", "hash", "none"] = "raw"
    retention_days: int = 730


class ServerConfig(Strict):
    version: int = 1
    data_dir: str = "/var/lib/constellation"
    http: HttpConfig = Field(default_factory=HttpConfig)
    https: HttpsConfig = Field(default_factory=HttpsConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    gopher: GopherConfig = Field(default_factory=GopherConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    studio: StudioProxyConfig = Field(default_factory=StudioProxyConfig)
    limits: Limits = Field(default_factory=Limits)
    analytics: AnalyticsServerConfig = Field(default_factory=AnalyticsServerConfig)

    @property
    def data(self) -> Path:
        return Path(self.data_dir)


def load_server_config(path: Path) -> ServerConfig:
    data = tomlkit.parse(Path(path).read_text(encoding="utf-8")).unwrap()
    return ServerConfig.model_validate(data)


def save_server_config(path: Path, cfg: ServerConfig) -> None:
    doc = tomlkit.document()
    for key, value in cfg.model_dump().items():
        doc[key] = value
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    # server.toml holds the admin token hash: keep it readable by its owner only, and keep
    # the owner the service user set at install time.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tomlkit.dumps(doc))
    os.chmod(tmp, 0o600)
    if hasattr(os, "chown") and path.exists():
        st = path.stat()
        with contextlib.suppress(OSError):
            os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)
