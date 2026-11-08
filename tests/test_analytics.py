"""The analytics pipeline: classification, identities, rollups, reports, bots, the API and the forwarder."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from starlette.testclient import TestClient

from constellation.server.analytics import Analytics, UmamiForwarder, classify, is_bot_ua

DAY = 86400


def test_classify_paths() -> None:
    assert classify("/") == ("home", None)
    assert classify("/index.gmi") == ("home", None)
    assert classify("/posts/the-call/") == ("post", "the-call")
    assert classify("/posts/the-call.gmi") == ("post", "the-call")
    assert classify("/tags/mending.gmi") == ("tag", "mending")
    assert classify("/feed.xml") == ("feed", None)
    assert classify("/media/posts/x/y.png") == ("media", None)
    assert classify("/assets/base/style.css") == ("media", None)
    assert classify("/about/") == ("page", "about")
    assert classify("/robots.txt") == ("other", None)
    assert is_bot_ua("Mozilla/5.0 (compatible; Googlebot/2.1)") and not is_bot_ua(
        "Mozilla/5.0 Firefox/120"
    )


def _events(a: Analytics, site: str, base: float) -> None:
    # Day 1: three web readers, one bot, a feed fetch; day 2: two gemini readers and a robots.txt crawler.
    for i, ip in enumerate(("203.0.113.1", "203.0.113.2", "203.0.113.1")):
        a.record(
            {
                "site": site,
                "platform": "web",
                "url": "/posts/the-call/",
                "status": 200,
                "ip": ip,
                "ts": base + i,
                "user_agent": "Mozilla/5.0 Firefox",
                "referrer": "https://news.example/thread/1",
                "host": "demo.example.org",
            }
        )
    a.record(
        {
            "site": site,
            "platform": "web",
            "url": "/posts/the-call/",
            "status": 200,
            "ip": "203.0.113.9",
            "ts": base + 5,
            "user_agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
        }
    )
    a.record(
        {
            "site": site,
            "platform": "web",
            "url": "/feed.xml",
            "status": 200,
            "ip": "203.0.113.3",
            "ts": base + 6,
            "user_agent": "Feedbin",
        }
    )
    a.record(
        {
            "site": site,
            "platform": "web",
            "url": "/nope/",
            "status": 404,
            "ip": "203.0.113.3",
            "ts": base + 7,
            "user_agent": "Mozilla/5.0",
        }
    )
    a.record(
        {
            "site": site,
            "platform": "gemini",
            "url": "gemini://demo.example.org/robots.txt",
            "status": 20,
            "ip": "198.51.100.7",
            "ts": base + DAY + 1,
        }
    )
    a.record(
        {
            "site": site,
            "platform": "gemini",
            "url": "gemini://demo.example.org/posts/the-call.gmi",
            "status": 20,
            "ip": "198.51.100.7",
            "ts": base + DAY + 2,
        }
    )
    a.record(
        {
            "site": site,
            "platform": "gemini",
            "url": "gemini://demo.example.org/posts/the-call.gmi",
            "status": 20,
            "ip": "198.51.100.8",
            "ts": base + DAY + 3,
            "tls_version": "TLSv1.3",
        }
    )
    a.record(
        {
            "site": site,
            "platform": "gemini",
            "url": "gemini://demo.example.org/",
            "status": 20,
            "ip": "198.51.100.9",
            "ts": base + DAY + 4,
        }
    )


def test_rollups_reports_and_bot_handling(tmp_path: Path) -> None:
    base = dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC).timestamp()
    now = base + 3 * DAY
    a = Analytics(tmp_path, ip_policy="raw")
    _events(a, "demo", base)
    ov = a.overview("demo", 7, now=now)
    by_day = {r["day"]: r for r in ov["series"]}
    d1, d2 = by_day["2026-11-01"], by_day["2026-11-02"]
    assert d1["platforms"]["web"] == {"views": 3, "visitors": 2, "bots": 1, "feeds": 1}
    assert d2["platforms"]["gemini"]["views"] == 2, (
        "the robots.txt crawler's post fetch is a bot hit"
    )
    assert d2["platforms"]["gemini"]["visitors"] == 2 and d2["platforms"]["gemini"]["bots"] == 2
    assert ov["totals"]["web"]["views"] == 3 and ov["totals"]["gemini"]["views"] == 2
    content = a.content("demo", 7, now=now)
    assert (
        content[0]["path"] == "/posts/the-call/"
        and content[0]["views"] == 3
        and content[0]["visitors"] == 2
    )
    assert any(
        c["platform"] == "gemini" and c["path"] == "/posts/the-call.gmi" and c["views"] == 1
        for c in content
    ), "the crawler is excluded"
    tech = a.technical("demo", 7, now=now)
    assert tech["statuses"]["404"] == 1 and tech["missing"][0]["path"] == "/nope/"
    assert tech["tls"].get("TLSv1.3") == 1 and 0 < tech["bot_share"] < 1
    refs = a.referrers("demo", 7, now=now)
    assert refs == [{"referrer": "news.example", "views": 3}]
    csv = a.export_csv("demo", 7)
    assert csv.startswith("day,platform,views")
    # Rollup moved finished days into the daily tables and kept raw events (retention is long).
    conn = a.db("demo")
    assert conn.execute("SELECT COUNT(*) FROM daily_totals").fetchone()[0] == 2
    assert (
        conn.execute("SELECT value FROM meta WHERE key='rolled_to'").fetchone()[0] == "2026-11-02"
    )
    # Today's events are counted live.
    a.record(
        {
            "site": "demo",
            "platform": "web",
            "url": "/about/",
            "status": 200,
            "ip": "203.0.113.5",
            "ts": now - 60,
            "user_agent": "Mozilla/5.0",
        }
    )
    ov2 = a.overview("demo", 7, now=now)
    assert by_day_today(ov2, now)["platforms"]["web"]["views"] == 1


def by_day_today(ov, now):
    today = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
    return next(r for r in ov["series"] if r["day"] == today)


def test_ip_policies(tmp_path: Path) -> None:
    ts = time.time()
    for policy, expect_ip in (
        ("raw", "203.0.113.1"),
        ("truncate", "203.0.113.0"),
        ("hash", None),
        ("none", None),
    ):
        a = Analytics(tmp_path / policy, ip_policy=policy)
        a.record(
            {
                "site": "s",
                "platform": "web",
                "url": "/",
                "status": 200,
                "ip": "203.0.113.1",
                "ts": ts,
                "user_agent": "UA",
            }
        )
        ip, visitor = a.db("s").execute("SELECT ip, visitor FROM events").fetchone()
        assert ip == expect_ip, policy
        assert (visitor == "") == (policy == "none"), policy
    a = Analytics(tmp_path / "hash2", ip_policy="hash")
    a.record(
        {
            "site": "s",
            "platform": "web",
            "url": "/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": ts,
            "user_agent": "UA",
        }
    )
    a.record(
        {
            "site": "s",
            "platform": "web",
            "url": "/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": ts + 1,
            "user_agent": "UA",
        }
    )
    a.record(
        {
            "site": "s",
            "platform": "web",
            "url": "/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": ts + 2 * DAY,
            "user_agent": "UA",
        }
    )
    visitors = [r[0] for r in a.db("s").execute("SELECT visitor FROM events ORDER BY ts")]
    assert visitors[0] == visitors[1] != visitors[2], (
        "hashed identities are stable within a day and change across days"
    )


def test_retention_purges_old_events(tmp_path: Path) -> None:
    a = Analytics(tmp_path, ip_policy="raw", retention_days=1)
    old = time.time() - 10 * DAY
    a.record(
        {
            "site": "s",
            "platform": "web",
            "url": "/posts/x/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": old,
            "user_agent": "UA",
        }
    )
    a.overview("s", 30)
    assert a.db("s").execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert a.db("s").execute("SELECT SUM(views) FROM daily_totals").fetchone()[0] == 1, (
        "rollups survive the purge"
    )


def test_umami_forwarder_payload_and_batching(tmp_path: Path) -> None:
    sent: list[tuple[str, list, str, str]] = []
    fwd = UmamiForwarder(
        send=lambda endpoint, bodies, ip, ua: sent.append((endpoint, bodies, ip, ua)), interval=60
    )
    cfg = {
        "analytics": {
            "forward_to_umami": {
                "enabled": True,
                "endpoint": "https://umami.example/api/send",
                "website_id": "w1",
            }
        },
        "hostnames": {"gemini": ["demo.example.org"]},
        "language": "en",
    }
    a = Analytics(tmp_path, ip_policy="raw", forwarder=fwd, site_configs=lambda sid: cfg)
    ts = time.time()
    a.record(
        {
            "site": "demo",
            "platform": "gemini",
            "url": "gemini://demo.example.org/posts/the-call.gmi",
            "status": 20,
            "ip": "198.51.100.8",
            "ts": ts,
        }
    )
    a.record(
        {
            "site": "demo",
            "platform": "gemini",
            "url": "gemini://demo.example.org/posts/other.gmi",
            "status": 20,
            "ip": "198.51.100.8",
            "ts": ts,
        }
    )
    a.record(
        {
            "site": "demo",
            "platform": "gemini",
            "url": "gemini://demo.example.org/robots.txt",
            "status": 20,
            "ip": "198.51.100.1",
            "ts": ts,
        }
    )
    a.record(
        {
            "site": "demo",
            "platform": "web",
            "url": "/posts/the-call/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": ts,
            "user_agent": "Mozilla",
        }
    )
    a.record(
        {
            "site": "demo",
            "platform": "gemini",
            "url": "gemini://demo.example.org/media/x.png",
            "status": 20,
            "ip": "198.51.100.8",
            "ts": ts,
        }
    )
    fwd.flush()
    assert len(sent) == 1, "one batch per reader address; web, robots and media are not forwarded"
    endpoint, bodies, ip, ua = sent[0]
    assert endpoint.endswith("/api/send") and ip == "198.51.100.8" and ua.startswith("Mozilla/5.0")
    assert [b["payload"]["url"] for b in bodies] == ["/posts/the-call.gmi", "/posts/other.gmi"]
    assert bodies[0]["payload"] == {
        "website": "w1",
        "hostname": "demo.example.org",
        "url": "/posts/the-call.gmi",
        "title": "the-call",
        "referrer": "",
        "language": "en",
        "name": "pageview",
        "data": {"platform": "gemini"},
    }
    assert fwd.sent == 2


def test_analytics_api_and_studio_page(tmp_path: Path) -> None:
    from constellation.server.config import load_server_config
    from constellation.server.http import HttpApp
    from constellation.server.install import install
    from constellation.server.releases import Store

    config_path, admin_token = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    app = HttpApp(cfg, Store(cfg.data))
    app.analytics = Analytics(cfg.data, ip_policy="raw")
    app.analytics.record(
        {
            "site": "demo",
            "platform": "web",
            "url": "/posts/x/",
            "status": 200,
            "ip": "203.0.113.1",
            "ts": time.time(),
            "user_agent": "Mozilla",
        }
    )
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "vps1.example",
    }
    with TestClient(app.app, base_url="https://vps1.example", headers=headers) as c:
        ov = c.get("/_constellation/sites/demo/analytics/overview?days=7").json()
        assert ov["totals"]["web"]["views"] == 1 and len(ov["series"]) == 7
        assert (
            c.get("/_constellation/sites/demo/analytics/content").json()["items"][0]["path"]
            == "/posts/x/"
        )
        assert (
            c.get("/_constellation/sites/demo/analytics/export.csv")
            .headers["content-type"]
            .startswith("text/csv")
        )
        assert c.get("/_constellation/sites/demo/analytics/bogus").status_code == 404
        assert (
            c.get(
                "/_constellation/sites/demo/analytics/overview",
                headers={"Authorization": "Bearer nope"},
            ).status_code
            == 401
        )
