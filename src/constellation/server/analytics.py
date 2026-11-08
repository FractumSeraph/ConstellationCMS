"""Server-side analytics: one event per request on every protocol, stored per site in SQLite,
rolled up per day, with an optional forwarder to the owner's Umami instance."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("constellation.server.analytics")

BOT_UA = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "curl/",
    "wget/",
    "python-requests",
    "go-http-client",
    "httpclient",
    "scrapy",
    "headless",
    "feedfetcher",
    "facebookexternalhit",
    "monitor",
    "uptime",
    "pingdom",
    "archive.org",
)
CONTENT_KINDS = (
    "post",
    "page",
    "tag",
    "category",
    "series",
    "archive",
    "feed",
    "media",
    "home",
    "other",
)


def classify(path: str) -> tuple[str, str | None]:
    """content kind and slug from a request path on any platform."""
    p = path.split("?", 1)[0]
    stripped = p.strip("/")
    if stripped in ("", "index.gmi", "index.html"):
        return "home", None
    if stripped in ("feed.xml", "feed.json", "atom.xml"):
        return "feed", None
    if stripped in ("robots.txt", "favicon.txt", "favicon.ico", "sitemap.xml"):
        return "other", None
    parts = stripped.split("/")
    first = parts[0]
    if first in ("media", "assets"):
        return "media", None
    if first in ("posts", "tags", "categories", "series", "archive") and len(parts) >= 2:
        kind = {
            "posts": "post",
            "tags": "tag",
            "categories": "category",
            "series": "series",
            "archive": "archive",
        }[first]
        slug = parts[1].rsplit(".", 1)[0] if "." in parts[1] else parts[1]
        return kind, slug or None
    if first in ("tags", "categories", "series", "archive", "page"):
        return "other", None
    slug = first.rsplit(".", 1)[0] if "." in first else first
    return "page", slug or None


def is_bot_ua(ua: str | None) -> bool:
    if not ua:
        return False
    u = ua.lower()
    return any(s in u for s in BOT_UA)


class Analytics:
    def __init__(
        self,
        data_dir: Path,
        *,
        ip_policy: str = "raw",
        retention_days: int = 730,
        forwarder: UmamiForwarder | None = None,
        site_configs: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.ip_policy = ip_policy
        self.retention_days = retention_days
        self.forwarder = forwarder
        self.site_configs = site_configs
        self._dbs: dict[str, sqlite3.Connection] = {}
        self._lock = threading.Lock()
        self._salts: dict[str, str] = {}
        self._bot_ips: dict[str, set[str]] = {}

    # ---- storage ----------------------------------------------------------------------------
    def db(self, site: str) -> sqlite3.Connection:
        with self._lock:
            conn = self._dbs.get(site)
            if conn is None:
                path = self.data_dir / "sites" / site / "analytics.sqlite"
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        ts REAL NOT NULL, platform TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL,
                        bytes INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, ip TEXT,
                        visitor TEXT, referrer TEXT, user_agent TEXT, is_bot INTEGER NOT NULL DEFAULT 0,
                        tls TEXT, kind TEXT NOT NULL, slug TEXT, host TEXT
                    );
                    CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
                    CREATE TABLE IF NOT EXISTS daily (
                        day TEXT NOT NULL, platform TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                        views INTEGER NOT NULL, visitors INTEGER NOT NULL, bots INTEGER NOT NULL,
                        PRIMARY KEY (day, platform, path)
                    );
                    CREATE TABLE IF NOT EXISTS daily_totals (
                        day TEXT NOT NULL, platform TEXT NOT NULL, views INTEGER NOT NULL, visitors INTEGER NOT NULL,
                        bots INTEGER NOT NULL, feed_fetches INTEGER NOT NULL, PRIMARY KEY (day, platform)
                    );
                    CREATE TABLE IF NOT EXISTS daily_referrers (
                        day TEXT NOT NULL, referrer TEXT NOT NULL, views INTEGER NOT NULL, PRIMARY KEY (day, referrer)
                    );
                    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    """
                )
                conn.commit()
                self._dbs[site] = conn
            return conn

    def close(self) -> None:
        with self._lock:
            for c in self._dbs.values():
                c.close()
            self._dbs.clear()

    # ---- recording ---------------------------------------------------------------------------
    def record(self, event: dict[str, Any]) -> None:
        site = str(event.get("site") or "")
        if not site:
            return
        ts = float(event.get("ts") or time.time())
        platform = str(event.get("platform", "web"))
        path = _path_of(str(event.get("url") or event.get("path") or "/"))
        ua = event.get("user_agent")
        ip = str(event.get("ip") or "")
        day = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")
        bots = self._bot_ips.setdefault(day, set())
        is_bot = is_bot_ua(ua) or (platform != "web" and ip in bots)
        if platform != "web" and path == "/robots.txt" and ip:
            bots.add(ip)
            is_bot = True
        kind, slug = classify(path)
        stored_ip, visitor = self._identity(day, ip, str(ua or ""), site)
        conn = self.db(site)
        with self._lock:
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    platform,
                    path,
                    int(event.get("status", 0)),
                    int(event.get("bytes", 0)),
                    int(event.get("duration_ms", 0)),
                    stored_ip,
                    visitor,
                    _referrer(event.get("referrer")),
                    (str(ua)[:300] if ua else None),
                    int(is_bot),
                    event.get("tls_version"),
                    kind,
                    slug,
                    event.get("host"),
                ),
            )
            conn.commit()
        if (
            self.forwarder is not None
            and not is_bot
            and platform != "web"
            and kind in ("post", "page", "home", "tag", "category", "series", "archive")
            and int(event.get("status", 0)) in (20, 200)
        ):
            cfg = self.site_configs(site) if self.site_configs else {}
            fwd = (cfg.get("analytics") or {}).get("forward_to_umami") or {}
            if fwd.get("enabled") and fwd.get("endpoint") and fwd.get("website_id"):
                self.forwarder.enqueue(
                    str(fwd["endpoint"]),
                    {
                        "type": "event",
                        "payload": {
                            "website": str(fwd["website_id"]),
                            "hostname": str(
                                event.get("host")
                                or (cfg.get("hostnames", {}).get(platform) or [""])[0]
                            ),
                            "url": path,
                            "title": slug or path,
                            "referrer": "",
                            "language": str(cfg.get("language", "en")),
                            "name": "pageview",
                            "data": {"platform": platform},
                        },
                    },
                    ip,
                    ua,
                )

    def _identity(self, day: str, ip: str, ua: str, site: str) -> tuple[str | None, str]:
        if not ip:
            return None, ""
        if self.ip_policy == "none":
            return None, ""
        if self.ip_policy == "raw":
            return ip, hashlib.sha256(f"{ip}|{ua}|{site}".encode()).hexdigest()[:24]
        if self.ip_policy == "truncate":
            return _truncate(ip), hashlib.sha256(
                f"{_truncate(ip)}|{ua}|{site}".encode()
            ).hexdigest()[:24]
        salt = self._daily_salt(day)
        return None, hashlib.sha256(f"{salt}|{ip}|{ua}|{site}".encode()).hexdigest()[:24]

    def _daily_salt(self, day: str) -> str:
        salt = self._salts.get(day)
        if salt is None:
            salt = secrets.token_hex(16)
            self._salts = {day: salt}
        return salt

    # ---- rollups ------------------------------------------------------------------------------
    def rollup(self, site: str, *, today: str | None = None) -> None:
        """Aggregate completed days from events into the daily tables; purge events past retention."""
        conn = self.db(site)
        today = today or dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
        with self._lock:
            row = conn.execute("SELECT value FROM meta WHERE key='rolled_to'").fetchone()
            rolled_to = row[0] if row else ""
            days = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT strftime('%Y-%m-%d', ts, 'unixepoch') AS d FROM events WHERE d < ? AND d > ? ORDER BY d",
                    (today, rolled_to),
                ).fetchall()
            ]
            for day in days:
                start = _day_start(day)
                end = start + 86400
                conn.execute("DELETE FROM daily WHERE day=?", (day,))
                conn.execute("DELETE FROM daily_totals WHERE day=?", (day,))
                conn.execute("DELETE FROM daily_referrers WHERE day=?", (day,))
                conn.execute(
                    """INSERT INTO daily SELECT ?, platform, kind, path, SUM(CASE WHEN is_bot=0 THEN 1 ELSE 0 END),
                       COUNT(DISTINCT CASE WHEN is_bot=0 AND visitor != '' THEN visitor END), SUM(is_bot)
                       FROM events WHERE ts >= ? AND ts < ? AND status IN (20, 200, 304) GROUP BY platform, path""",
                    (day, start, end),
                )
                conn.execute(
                    """INSERT INTO daily_totals SELECT ?, platform, SUM(CASE WHEN is_bot=0 AND kind NOT IN ('media','other','feed') THEN 1 ELSE 0 END),
                       COUNT(DISTINCT CASE WHEN is_bot=0 AND visitor != '' AND kind NOT IN ('media','other','feed') THEN visitor END),
                       SUM(is_bot), SUM(CASE WHEN kind='feed' AND is_bot=0 THEN 1 ELSE 0 END)
                       FROM events WHERE ts >= ? AND ts < ? AND status IN (20, 200, 304) GROUP BY platform""",
                    (day, start, end),
                )
                conn.execute(
                    """INSERT INTO daily_referrers SELECT ?, referrer, COUNT(*) FROM events
                       WHERE ts >= ? AND ts < ? AND is_bot=0 AND referrer IS NOT NULL AND referrer != '' GROUP BY referrer""",
                    (day, start, end),
                )
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('rolled_to', ?)", (day,))
            cutoff = time.time() - self.retention_days * 86400
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
            stale = [d for d in self._bot_ips if d < today]
            for d in stale:
                del self._bot_ips[d]

    # ---- reports -----------------------------------------------------------------------------
    def overview(self, site: str, days: int = 30, *, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        today = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
        self.rollup(site, today=today)
        conn = self.db(site)
        start_day = (dt.datetime.fromtimestamp(now, dt.UTC) - dt.timedelta(days=days - 1)).strftime(
            "%Y-%m-%d"
        )
        rows: dict[str, dict[str, dict[str, int]]] = {}
        for day, platform, views, visitors, bots, feeds in conn.execute(
            "SELECT day, platform, views, visitors, bots, feed_fetches FROM daily_totals WHERE day >= ? AND day < ?",
            (start_day, today),
        ):
            rows.setdefault(day, {})[platform] = {
                "views": views,
                "visitors": visitors,
                "bots": bots,
                "feeds": feeds,
            }
        live = conn.execute(
            """SELECT platform, SUM(CASE WHEN is_bot=0 AND kind NOT IN ('media','other','feed') THEN 1 ELSE 0 END),
               COUNT(DISTINCT CASE WHEN is_bot=0 AND visitor != '' AND kind NOT IN ('media','other','feed') THEN visitor END),
               SUM(is_bot), SUM(CASE WHEN kind='feed' AND is_bot=0 THEN 1 ELSE 0 END)
               FROM events WHERE ts >= ? AND status IN (20, 200, 304) GROUP BY platform""",
            (_day_start(today),),
        ).fetchall()
        for platform, views, visitors, bots, feeds in live:
            rows.setdefault(today, {})[platform] = {
                "views": views or 0,
                "visitors": visitors or 0,
                "bots": bots or 0,
                "feeds": feeds or 0,
            }
        series = []
        d = dt.datetime.strptime(start_day, "%Y-%m-%d")
        totals: dict[str, dict[str, int]] = {}
        for _ in range(days):
            key = d.strftime("%Y-%m-%d")
            per = rows.get(key, {})
            series.append(
                {
                    "day": key,
                    "platforms": per,
                    "views": sum(v["views"] for v in per.values()),
                    "visitors": sum(v["visitors"] for v in per.values()),
                }
            )
            for platform, v in per.items():
                t = totals.setdefault(platform, {"views": 0, "visitors": 0, "bots": 0, "feeds": 0})
                for k in t:
                    t[k] += v[k]
            d += dt.timedelta(days=1)
        return {"days": days, "series": series, "totals": totals}

    def content(
        self, site: str, days: int = 30, limit: int = 25, *, now: float | None = None
    ) -> list[dict[str, Any]]:
        now = now or time.time()
        today = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
        self.rollup(site, today=today)
        conn = self.db(site)
        start_day = (dt.datetime.fromtimestamp(now, dt.UTC) - dt.timedelta(days=days - 1)).strftime(
            "%Y-%m-%d"
        )
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        for platform, kind, path, views, visitors in conn.execute(
            "SELECT platform, kind, path, SUM(views), SUM(visitors) FROM daily WHERE day >= ? AND day < ? AND kind IN ('post','page','home') GROUP BY platform, path",
            (start_day, today),
        ):
            agg[(platform, path)] = {
                "platform": platform,
                "kind": kind,
                "path": path,
                "views": views,
                "visitors": visitors,
            }
        for platform, kind, path, views, visitors in conn.execute(
            """SELECT platform, kind, path, COUNT(*), COUNT(DISTINCT CASE WHEN visitor != '' THEN visitor END) FROM events
                   WHERE ts >= ? AND is_bot=0 AND status IN (20,200,304) AND kind IN ('post','page','home') GROUP BY platform, path""",
            (_day_start(today),),
        ):
            row = agg.setdefault(
                (platform, path),
                {"platform": platform, "kind": kind, "path": path, "views": 0, "visitors": 0},
            )
            row["views"] += views
            row["visitors"] += visitors
        return sorted(agg.values(), key=lambda r: (-r["views"], r["path"]))[:limit]

    def technical(self, site: str, days: int = 30, *, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        conn = self.db(site)
        since = now - days * 86400
        statuses = {
            str(s): n
            for s, n in conn.execute(
                "SELECT status, COUNT(*) FROM events WHERE ts >= ? GROUP BY status", (since,)
            )
        }
        missing = [
            {"path": p, "hits": n}
            for p, n in conn.execute(
                "SELECT path, COUNT(*) FROM events WHERE ts >= ? AND status IN (404, 51) GROUP BY path ORDER BY 2 DESC LIMIT 20",
                (since,),
            )
        ]
        tls = {
            str(t or "none"): n
            for t, n in conn.execute(
                "SELECT tls, COUNT(*) FROM events WHERE ts >= ? GROUP BY tls", (since,)
            )
        }
        bots = conn.execute(
            "SELECT SUM(is_bot), COUNT(*) FROM events WHERE ts >= ?", (since,)
        ).fetchone()
        agents = [
            {"agent": a, "hits": n}
            for a, n in conn.execute(
                "SELECT user_agent, COUNT(*) FROM events WHERE ts >= ? AND user_agent IS NOT NULL AND is_bot=0 GROUP BY user_agent ORDER BY 2 DESC LIMIT 15",
                (since,),
            )
        ]
        return {
            "statuses": statuses,
            "missing": missing,
            "tls": tls,
            "bot_share": (bots[0] or 0) / bots[1] if bots and bots[1] else 0.0,
            "agents": agents,
            "note": "Raw events cover the retention window; the day being counted is live.",
        }

    def referrers(
        self, site: str, days: int = 30, *, now: float | None = None
    ) -> list[dict[str, Any]]:
        now = now or time.time()
        today = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
        self.rollup(site, today=today)
        conn = self.db(site)
        start_day = (dt.datetime.fromtimestamp(now, dt.UTC) - dt.timedelta(days=days - 1)).strftime(
            "%Y-%m-%d"
        )
        agg: dict[str, int] = {}
        for ref, views in conn.execute(
            "SELECT referrer, SUM(views) FROM daily_referrers WHERE day >= ? AND day < ? GROUP BY referrer",
            (start_day, today),
        ):
            agg[ref] = agg.get(ref, 0) + views
        for ref, views in conn.execute(
            "SELECT referrer, COUNT(*) FROM events WHERE ts >= ? AND is_bot=0 AND referrer IS NOT NULL AND referrer != '' GROUP BY referrer",
            (_day_start(today),),
        ):
            agg[ref] = agg.get(ref, 0) + views
        return [
            {"referrer": r, "views": v}
            for r, v in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
        ]

    def export_csv(self, site: str, days: int = 30) -> str:
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["day", "platform", "views", "visitors", "bots", "feed_fetches"])
        for row in self.overview(site, days)["series"]:
            for platform, v in sorted(row["platforms"].items()):
                w.writerow([row["day"], platform, v["views"], v["visitors"], v["bots"], v["feeds"]])
        return buf.getvalue()


class UmamiForwarder:
    """Batches page views to Umami's /api/send with the reader's address in X-Forwarded-For."""

    def __init__(
        self,
        *,
        user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) ConstellationCMS/1.0",
        send: Callable[[str, list[dict[str, Any]], str, str], None] | None = None,
        interval: float = 5.0,
    ) -> None:
        self.user_agent = user_agent
        self.queue: queue.Queue[tuple[str, dict[str, Any], str, str]] = queue.Queue(maxsize=10000)
        self.send = send or self._send_http
        self.interval = interval
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.sent = 0
        self.failed = 0

    def enqueue(self, endpoint: str, body: dict[str, Any], ip: str, ua: str | None) -> None:
        try:
            self.queue.put_nowait((endpoint, body, ip, ua or ""))
        except queue.Full:
            self.failed += 1

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True, name="umami-forwarder")
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.flush()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.flush()

    def flush(self) -> None:
        batches: dict[tuple[str, str], list[dict[str, Any]]] = {}
        while True:
            try:
                endpoint, body, ip, ua = self.queue.get_nowait()
            except queue.Empty:
                break
            batches.setdefault((endpoint, ip), []).append(body)
        for (endpoint, ip), bodies in batches.items():
            try:
                self.send(endpoint, bodies, ip, self.user_agent)
                self.sent += len(bodies)
            except Exception as e:  # noqa: BLE001
                self.failed += len(bodies)
                log.warning("umami forward failed: %s", e)

    def _send_http(self, endpoint: str, bodies: list[dict[str, Any]], ip: str, ua: str) -> None:
        import httpx

        headers = {"User-Agent": ua, "Content-Type": "application/json"}
        if ip:
            headers["X-Forwarded-For"] = ip
        for body in bodies:
            body["payload"]["ip"] = ip
        batch_url = (
            endpoint.rsplit("/", 1)[0] + "/batch" if endpoint.endswith("/send") else endpoint
        )
        with httpx.Client(timeout=10) as client:
            if len(bodies) == 1:
                client.post(endpoint, json=bodies[0], headers=headers).raise_for_status()
            else:
                client.post(batch_url, json=bodies, headers=headers).raise_for_status()


def _path_of(url: str) -> str:
    if "://" in url:
        return urlsplit(url).path or "/"
    return url.split("?", 1)[0] or "/"


def _referrer(ref: Any) -> str | None:
    if not ref:
        return None
    try:
        host = urlsplit(str(ref)).hostname
    except ValueError:
        return None
    return (host or str(ref))[:200]


def _truncate(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False).network_address)
    return str(ipaddress.ip_network(f"{ip}/48", strict=False).network_address)


def _day_start(day: str) -> float:
    return dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
