"""The studio/CLI side of the publish protocol."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from constellation.core.build.pipeline import Build
from constellation.core.models import Site


class PublishError(Exception):
    pass


@dataclass(slots=True)
class PublishReport:
    release_id: str
    uploaded: int
    skipped: int
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    activated: bool = False
    pruned: list[str] = field(default_factory=list)


def site_config_for_release(site: Site) -> dict[str, Any]:
    cfg = site.config
    hostnames = {
        p: list(cfg.platforms[p].hostnames)
        for p in cfg.enabled_platforms()
        if cfg.platforms[p].hostnames
    }
    canonical = {p: hosts[0] for p, hosts in hostnames.items()}
    return {
        "hostnames": hostnames,
        "canonical": canonical,
        "language": cfg.site.language,
        "redirects": [{"from": r.from_path, "to": r.to, "status": r.status} for r in cfg.redirects],
        "headers": {"web": {"Strict-Transport-Security": "max-age=31536000", **cfg.headers}},
        "analytics": {"forward_to_umami": cfg.analytics.forward_to_umami.model_dump()},
    }


def release_manifest(build: Build, site: Site) -> dict[str, Any]:
    m = build.manifest()
    m["generator"] = "constellationcms"
    m["site_config"] = site_config_for_release(site)
    return m


class PublishClient:
    """Talks to ``<admin_url>`` (``https://host/_constellation``). Tests inject an ``httpx.Client``."""

    def __init__(
        self,
        admin_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 120,
    ) -> None:
        from urllib.parse import urlsplit

        parts = urlsplit(admin_url.rstrip("/"))
        if parts.scheme != "https" and not (client is not None):
            raise PublishError("the admin URL must use https")
        self.prefix = parts.path.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "User-Agent": "constellation-publish"}
        self.client = client or httpx.Client(
            base_url=f"{parts.scheme}://{parts.netloc}", timeout=timeout
        )
        self.client.headers.update(self.headers)

    def close(self) -> None:
        self.client.close()

    def _url(self, path: str) -> str:
        return self.prefix + path

    def _check(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", r.text)
            except ValueError:
                msg = r.text
            raise PublishError(f"{r.request.method} {r.request.url.path}: {r.status_code} {msg}")
        return dict(r.json()) if r.content else {}

    def current_manifest(self, site: str) -> dict[str, Any] | None:
        r = self.client.get(self._url(f"/sites/{site}/manifest"))
        if r.status_code == 404:
            return None
        return self._check(r)

    def releases(self, site: str) -> dict[str, Any]:
        return self._check(self.client.get(self._url(f"/sites/{site}/releases")))

    def activate(self, site: str, rid: str) -> dict[str, Any]:
        return self._check(self.client.post(self._url(f"/sites/{site}/releases/{rid}/activate")))

    def mint_token(self, site: str, label: str) -> dict[str, Any]:
        return self._check(
            self.client.post(self._url(f"/sites/{site}/tokens"), json={"label": label})
        )

    def analytics(self, site: str, report: str, days: int = 30) -> dict[str, Any]:
        return self._check(
            self.client.get(self._url(f"/sites/{site}/analytics/{report}"), params={"days": days})
        )

    def server_config(self) -> dict[str, Any]:
        return self._check(self.client.get(self._url("/server/config")))

    def update_server_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self._check(self.client.put(self._url("/server/config"), json={"changes": changes}))

    def server_certs(self) -> dict[str, Any]:
        return self._check(self.client.get(self._url("/server/certs")))

    def regenerate_gemini_cert(self, host: str) -> dict[str, Any]:
        return self._check(self.client.post(self._url(f"/server/certs/gemini/{host}/regenerate")))

    def retire(self, site: str) -> dict[str, Any]:
        return self._check(self.client.delete(self._url(f"/sites/{site}")))

    def status(self) -> dict[str, Any]:
        return self._check(self.client.get(self._url("/status")))

    def plan(self, site: str, manifest: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
        """Diff against the active release and the list of blobs the server lacks (no side effects)."""
        from constellation.core.build.pipeline import diff_manifests

        current = self.current_manifest(site) or {"platforms": {}}
        diff: dict[str, list[str]] = {"added": [], "changed": [], "removed": []}
        for platform in sorted(set(manifest["platforms"]) | set(current.get("platforms", {}))):
            d = diff_manifests(
                current.get("platforms", {}).get(platform, {}),
                manifest["platforms"].get(platform, {}),
            )
            for k in diff:
                diff[k].extend(f"{platform}/{p}" for p in d[k])
        res = self._check(
            self.client.post(
                self._url(f"/sites/{site}/releases"), params={"dry_run": "1"}, json=manifest
            )
        )
        return diff, list(res.get("missing", []))

    def publish(
        self,
        site: str,
        manifest: dict[str, Any],
        blobs: Callable[[str], bytes],
        *,
        progress: Callable[[str], None] | None = None,
    ) -> PublishReport:
        say = progress or (lambda s: None)
        diff, _ = self.plan(site, manifest)
        res = self._check(self.client.post(self._url(f"/sites/{site}/releases"), json=manifest))
        rid, missing = str(res["release_id"]), list(res["missing"])
        say(f"release {rid}: {len(missing)} blobs to upload")
        uploaded = 0
        for sha in missing:
            data = blobs(sha)
            if hashlib.sha256(data).hexdigest() != sha:
                raise PublishError(f"local blob {sha} does not match its hash")
            self._check(
                self.client.put(
                    self._url(f"/sites/{site}/blobs/{sha}"),
                    content=data,
                    headers={"Content-Type": "application/octet-stream"},
                )
            )
            uploaded += 1
            if uploaded % 25 == 0:
                say(f"uploaded {uploaded}/{len(missing)}")
        res = self._check(self.client.post(self._url(f"/sites/{site}/releases/{rid}/commit")))
        say(f"release {rid} is active")
        unique = {e["sha256"] for files in manifest["platforms"].values() for e in files.values()}
        return PublishReport(
            rid,
            uploaded,
            len(unique) - uploaded,
            diff["added"],
            diff["changed"],
            diff["removed"],
            res.get("status") == "active",
            list(res.get("pruned", [])),
        )


def blob_reader(build: Build) -> Callable[[str], bytes]:
    index: dict[str, Any] = {}
    for pb in build.platforms.values():
        for f in pb.files.values():
            index.setdefault(f.sha256, f)

    def read(sha: str) -> bytes:
        if sha not in index:
            raise PublishError(f"server asked for unknown blob {sha}")
        return bytes(index[sha].bytes())

    return read


def manifest_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, sort_keys=True)
