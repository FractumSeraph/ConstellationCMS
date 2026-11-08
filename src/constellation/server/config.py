"""server.toml: listeners, TLS mode, admin settings, limits."""

from __future__ import annotations

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
    port: int = 70
    site: str = ""


class AdminConfig(Strict):
    hostname: str = ""
    token_hash: str = ""


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
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    tmp.replace(path)
