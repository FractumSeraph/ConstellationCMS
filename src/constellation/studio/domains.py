"""Which DNS records a site needs, and whether they are right yet.

The studio asks a public DNS-over-HTTPS resolver (Cloudflare's, then Google's), so the answer
is what the rest of the internet sees rather than this computer's cache. A site published to a
server needs its names to point at that server; a site whose web version goes to GitHub Pages
needs GitHub's addresses for the web and the server's for Gemini and Gopher.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

RESOLVERS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
GITHUB_PAGES_IPS = ("185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153")
TYPES = {"A": 1, "AAAA": 28, "CNAME": 5}
# Cloudflare's proxy addresses: a name "through Cloudflare" (orange cloud) answers with these.
CLOUDFLARE = tuple(
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
    )
)


def is_cloudflare(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in CLOUDFLARE)


def lookup(name: str, rtype: str = "A", *, client: httpx.Client | None = None) -> list[str]:
    """The current answers for ``name`` (empty when there are none or no resolver answered)."""
    own = client is None
    c = client or httpx.Client(timeout=8)
    try:
        for url in RESOLVERS:
            try:
                r = c.get(
                    url,
                    params={"name": name, "type": rtype},
                    headers={"accept": "application/dns-json"},
                )
                data = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            want = TYPES[rtype]
            return [
                str(a["data"]).rstrip(".") for a in data.get("Answer", []) if a.get("type") == want
            ]
        return []
    finally:
        if own:
            c.close()


@dataclass(slots=True)
class Record:
    rtype: str
    name: str
    value: str  # what it should be
    why: str
    found: list[str] = field(default_factory=list)

    @property
    def proxied(self) -> bool:
        """Answered by Cloudflare's proxy (the orange cloud) instead of the real address."""
        return bool(self.found) and all(is_cloudflare(ip) for ip in self.found)

    @property
    def ok(self) -> bool:
        # The web works through Cloudflare's proxy; Gemini and Gopher can't go through it.
        web_only = "gemini" not in self.why and "gopher" not in self.why
        if self.value in self.found or (self.proxied and web_only):
            return True
        # A www name on GitHub Pages may answer with GitHub's addresses instead of the CNAME.
        return (
            self.why == "web, on GitHub Pages"
            and bool(self.found)
            and all(ip in GITHUB_PAGES_IPS for ip in self.found)
        )


@dataclass(slots=True)
class DomainReport:
    records: list[Record]
    server_ip: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.records) and all(r.ok for r in self.records)


def _hosts(pcfg: Any) -> list[str]:
    hosts = [h.lower() for h in getattr(pcfg, "hostnames", [])]
    base = urlsplit(getattr(pcfg, "base_url", "") or "").hostname
    if base and base.lower() not in hosts:
        hosts.insert(0, base.lower())
    return hosts


def expected_records(
    cfg: Any, server_admin_url: str, *, client: httpx.Client | None = None
) -> DomainReport:
    """The records ``cfg`` (a SiteConfig) needs, with what DNS says right now."""
    admin_host = urlsplit(server_admin_url).hostname or "" if server_admin_url else ""
    server_ips = lookup(admin_host, "A", client=client) if admin_host else []
    server_ip = server_ips[0] if server_ips else ""
    pages_targets = [
        e.target
        for e in cfg.exports
        if e.platform == "web" and e.target.startswith(("github:", "git:"))
    ]
    pages = bool(pages_targets)
    owner = next(
        (t[len("github:") :].split("/", 1)[0] for t in pages_targets if t.startswith("github:")), ""
    )
    records: list[Record] = []
    notes: list[str] = []
    missing_server = False

    def need(rtype: str, name: str, value: str, why: str) -> None:
        nonlocal missing_server
        if not value:
            missing_server = missing_server or why in ("web", "gemini", "gopher")
            return
        for r in records:
            if (r.name, r.value) == (name, value):
                if why not in r.why:
                    r.why += ", " + why  # one record serving several platforms
                return
        records.append(Record(rtype, name, value, why))

    web = cfg.platform("web")
    if web.enabled:
        for host in _hosts(web):
            if pages:
                if host.count(".") == 1:  # an apex domain: GitHub's four addresses
                    for ip in GITHUB_PAGES_IPS:
                        need("A", host, ip, "web, on GitHub Pages")
                else:
                    need(
                        "CNAME",
                        host,
                        f"{owner.lower()}.github.io" if owner else "",
                        "web, on GitHub Pages",
                    )
            else:
                need("A", host, server_ip, "web")
    for platform in ("gemini", "gopher"):
        pcfg = cfg.platform(platform)
        if pcfg.enabled:
            for host in _hosts(pcfg):
                need("A", host, server_ip, platform)
    if pages:
        notes.append(
            "Names on GitHub Pages also need the domain set in the repository's Pages settings; publishing does that for you."
        )
    if missing_server:
        notes.append(
            "This site isn't assigned to a server yet (or the server's address doesn't answer), so "
            "the server's IP address is unknown. Choose a server on the Servers page first."
        )
    for r in records:
        r.found = lookup(r.name, r.rtype, client=client)
        if r.rtype == "CNAME" and not r.found:
            # Cloudflare's proxy (and "CNAME flattening") answer a CNAME name with addresses.
            r.found = lookup(r.name, "A", client=client)
    return DomainReport(records, server_ip, notes)


def https_works(host: str, *, client: httpx.Client | None = None) -> bool:
    own = client is None
    c = client or httpx.Client(timeout=8, follow_redirects=True)
    try:
        return c.get(f"https://{host}/").status_code < 500
    except httpx.HTTPError:
        return False
    finally:
        if own:
            c.close()
