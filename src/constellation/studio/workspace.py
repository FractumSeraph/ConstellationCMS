"""The workspace: ``workspace.toml`` with the site and server registries, and site scaffolding."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit
from slugify import slugify

SITE_ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class ServerEntry:
    id: str
    name: str
    admin_url: str


@dataclass
class SiteEntry:
    id: str
    name: str
    server: str = ""
    archived: bool = False


@dataclass
class Workspace:
    root: Path
    servers: list[ServerEntry] = field(default_factory=list)
    sites: list[SiteEntry] = field(default_factory=list)

    @property
    def toml_path(self) -> Path:
        return self.root / "workspace.toml"

    def site_dir(self, site_id: str) -> Path:
        if not SITE_ID_RX.match(site_id):
            raise ValueError(f"invalid site id {site_id!r}")
        return self.root / "sites" / site_id

    def site(self, site_id: str) -> SiteEntry | None:
        return next((s for s in self.sites if s.id == site_id), None)

    def server(self, server_id: str) -> ServerEntry | None:
        return next((s for s in self.servers if s.id == server_id), None)

    def themes_dir(self) -> Path:
        return self.root / "themes"


def load_workspace(root: Path) -> Workspace:
    root = Path(root)
    ws = Workspace(root=root)
    if ws.toml_path.is_file():
        data = tomlkit.parse(ws.toml_path.read_text(encoding="utf-8"))
        for s in data.get("servers", []):
            ws.servers.append(
                ServerEntry(
                    str(s.get("id", "")), str(s.get("name", "")), str(s.get("admin_url", ""))
                )
            )
        for s in data.get("sites", []):
            ws.sites.append(
                SiteEntry(
                    str(s.get("id", "")),
                    str(s.get("name", "")),
                    str(s.get("server", "")),
                    bool(s.get("archived", False)),
                )
            )
    return ws


def save_workspace(ws: Workspace) -> None:
    ws.root.mkdir(parents=True, exist_ok=True)
    if ws.toml_path.is_file():
        doc = tomlkit.parse(ws.toml_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        doc["version"] = 1
    servers = tomlkit.aot()
    for srv in ws.servers:
        t = tomlkit.table()
        t["id"], t["name"], t["admin_url"] = srv.id, srv.name, srv.admin_url
        servers.append(t)
    sites = tomlkit.aot()
    for site in ws.sites:
        t2 = tomlkit.table()
        t2["id"], t2["name"], t2["server"] = site.id, site.name, site.server
        if site.archived:
            t2["archived"] = True
        sites.append(t2)
    doc["servers"] = servers
    doc["sites"] = sites
    atomic_write(ws.toml_path, tomlkit.dumps(doc))


def atomic_write(path: Path, text: str | bytes) -> None:
    """Write via a temporary file in the same folder, then replace, so sync clients never see halves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if isinstance(text, str):
        tmp.write_text(text, encoding="utf-8", newline="\n")
    else:
        tmp.write_bytes(text)
    try:
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    os.replace(tmp, path)


def create_site(
    ws: Workspace,
    name: str,
    *,
    site_id: str | None = None,
    web_url: str = "",
    gemini_url: str = "",
    timezone: str = "UTC",
    author: str = "",
    theme: str = "base",
) -> SiteEntry:
    sid = site_id or slugify(name)
    if not SITE_ID_RX.match(sid):
        raise ValueError(f"site id {sid!r} must be lowercase letters, digits and hyphens")
    if ws.site(sid):
        raise ValueError(f"site {sid!r} already exists")
    root = ws.site_dir(sid)
    for sub in (
        "content/posts",
        "content/pages",
        "content/tags",
        "content/categories",
        "content/series",
        "media",
        "theme",
        ".constellation",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    doc["version"] = 1
    site = tomlkit.table()
    site["title"] = name
    site["tagline"] = ""
    site["language"] = "en"
    site["timezone"] = timezone
    site["theme"] = theme or "base"
    site["author"] = slugify(author) if author else ""
    doc["site"] = site
    if author:
        authors = tomlkit.aot()
        a = tomlkit.table()
        a["id"], a["name"] = slugify(author), author
        authors.append(a)
        doc["authors"] = authors
    platforms = tomlkit.table()
    web = tomlkit.table()
    web["enabled"] = True
    web["base_url"] = web_url.rstrip("/")
    web["hostnames"] = [_host(web_url)] if _host(web_url) else []
    web["pagination"] = 10
    web["feeds"] = ["atom", "json"]
    platforms["web"] = web
    gem = tomlkit.table()
    gem["enabled"] = bool(gemini_url)
    gem["base_url"] = gemini_url.rstrip("/")
    gem["hostnames"] = [_host(gemini_url)] if _host(gemini_url) else []
    gem["feeds"] = ["atom", "gemsub"]
    platforms["gemini"] = gem
    doc["platforms"] = platforms
    menu = tomlkit.table()
    menu["items"] = [{"label": "Home", "ref": "home"}]
    doc["menu"] = menu
    doc["footer"] = {"text": ""}
    doc["seo"] = {"default_description": ""}
    doc["custom_html"] = {"head": "", "body_start": "", "body_end": ""}
    atomic_write(root / "site.toml", tomlkit.dumps(doc))
    entry = SiteEntry(id=sid, name=name)
    ws.sites.append(entry)
    save_workspace(ws)
    return entry


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or "" if url else ""


def read_site_toml(root: Path) -> tomlkit.TOMLDocument:
    return tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))


def write_site_toml(root: Path, doc: tomlkit.TOMLDocument) -> None:
    atomic_write(root / "site.toml", tomlkit.dumps(doc))


def set_path(doc: Any, dotted: str, value: Any) -> None:
    """Set a nested key like ``platforms.web.base_url`` on a tomlkit document, creating tables."""
    parts = dotted.split(".")
    node = doc
    for part in parts[:-1]:
        if part not in node:
            node[part] = tomlkit.table()
        node = node[part]
    node[parts[-1]] = value
