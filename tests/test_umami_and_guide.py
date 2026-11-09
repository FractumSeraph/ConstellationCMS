"""Umami from a pasted tracking code, and the studio's server guide and setup helpers."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomlkit

from constellation.core import umami
from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.client import release_manifest
from tests.test_studio import _csrf, studio  # noqa: F401  (pytest fixture)

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
WID = "0b9f3c1e-6a2d-4c8e-9f11-2a7d5e8c4b10"
CODE = f'<script defer src="https://cloud.umami.is/script.js" data-website-id="{WID}"></script>'
PASSWORD = "correct horse battery"


# ---- reading the tracking code ----------------------------------------------------------------
def test_parses_the_code_umami_gives_you() -> None:
    code = umami.parse_tracking_code(CODE)
    assert code.script_url == "https://cloud.umami.is/script.js"
    assert code.website_id == WID
    assert code.send_endpoint == "https://cloud.umami.is/api/send"


def test_parses_other_quoting_order_and_a_separate_api_host() -> None:
    text = (
        f"  <!-- Umami -->\n<script data-website-id='{WID.upper()}' "
        "data-host-url='https://stats.example.org/' src=https://cdn.example.org/u.js async></script>"
    )
    code = umami.parse_tracking_code(text)
    assert code.website_id == WID  # normalised to lower case
    assert code.script_url == "https://cdn.example.org/u.js"
    assert code.send_endpoint == "https://stats.example.org/api/send"


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "Paste the tracking code"),
        ("hello", "isn't a tracking code"),
        ('<script src="https://x.example/a.js"></script>', "no data-website-id"),
        (
            '<script src="https://x.example/a.js" data-website-id="abc"></script>',
            "doesn't look like",
        ),
        (f'<script data-website-id="{WID}"></script>', "no script address"),
        (f'<script src="http://x.example/s.js" data-website-id="{WID}"></script>', "http://"),
        (f'<script src="javascript:alert(1)" data-website-id="{WID}"></script>', "https://"),
    ],
)
def test_explains_what_is_wrong_with_a_paste(text: str, says: str) -> None:
    with pytest.raises(umami.UmamiCodeError, match=re.escape(says)):
        umami.parse_tracking_code(text)


def test_script_tag_limits_counting_to_the_sites_domains_and_escapes() -> None:
    code = umami.UmamiCode('https://u.example/s.js?a=1&b="2"', WID)
    tag = umami.script_tag(code, ["Example.org", "www.example.org", "example.org", ""])
    assert tag.startswith("<script defer ")
    assert 'data-domains="example.org,www.example.org"' in tag
    assert "&amp;b=&quot;2&quot;" in tag
    assert "data-domains" not in umami.script_tag(code, [])


def test_moves_a_hand_pasted_copy_out_of_custom_html() -> None:
    head = f"<meta name=x>\n  {CODE}\n<!-- keep -->"
    new, moved = umami.strip_matching_snippet(head, WID)
    assert moved and WID not in new and "<meta name=x>" in new and "<!-- keep -->" in new
    other = '<script defer src="https://cloud.umami.is/script.js" data-website-id="11111111-2222-3333-4444-555555555555"></script>'
    assert umami.strip_matching_snippet(other, WID) == (other, False)


# ---- the build and the release ----------------------------------------------------------------
def _site_with(tmp_path: Path, umami_table: str = "", head: str = "") -> Path:
    root = tmp_path / "site"
    shutil.copytree(DEMO, root)
    toml = root / "site.toml"
    doc = tomlkit.parse(toml.read_text(encoding="utf-8"))
    if head:
        doc["custom_html"]["head"] = head
    text = tomlkit.dumps(doc) + umami_table
    toml.write_text(text, encoding="utf-8")
    return root


UMAMI_TABLE = (
    f'\n[analytics.umami]\nscript_url = "https://cloud.umami.is/script.js"\nwebsite_id = "{WID}"\n'
)


def test_every_web_page_gets_the_script_once(tmp_path: Path) -> None:
    site = load_site(_site_with(tmp_path, UMAMI_TABLE))
    build = build_site(site, now=NOW)
    web = build.platforms["web"]
    pages = [f for f in web.files.values() if f.media_type.startswith("text/html")]
    assert len(pages) > 5
    for f in pages:
        text = f.bytes().decode("utf-8")
        assert text.count(WID) == 1, f.path
        assert text.index(WID) < text.lower().index("</head>")
        assert 'data-domains="demo.example.org,www.demo.example.org"' in text
    assert not any(w.code.startswith("UMAMI") for w in web.warnings)
    for platform in ("gemini", "gopher"):
        if platform in build.platforms:
            for f in build.platforms[platform].files.values():
                assert WID.encode() not in f.bytes()


def test_no_umami_means_no_script(tmp_path: Path) -> None:
    build = build_site(load_site(_site_with(tmp_path)), now=NOW)
    assert not any(b"umami" in f.bytes() for f in build.platforms["web"].files.values())


def test_a_copy_left_in_custom_html_is_not_loaded_twice(tmp_path: Path) -> None:
    build = build_site(load_site(_site_with(tmp_path, UMAMI_TABLE, head=CODE)), now=NOW)
    web = build.platforms["web"]
    index = web.files["index.html"].bytes().decode("utf-8")
    assert index.count(WID) == 1
    assert any(w.code == "UMAMI-DUPLICATE" for w in web.warnings)


def test_the_release_tells_the_server_where_to_forward_gemini_and_gopher(tmp_path: Path) -> None:
    site = load_site(_site_with(tmp_path, UMAMI_TABLE))
    fwd = release_manifest(build_site(site, now=NOW), site)["site_config"]["analytics"][
        "forward_to_umami"
    ]
    assert fwd == {
        "enabled": True,
        "endpoint": "https://cloud.umami.is/api/send",
        "website_id": WID,
    }

    off = load_site(_site_with(tmp_path / "off", UMAMI_TABLE + "forward_other_platforms = false\n"))
    assert umami.forward_settings(off.config)["enabled"] is False

    manual = UMAMI_TABLE + (
        '\n[analytics.forward_to_umami]\nenabled = true\nendpoint = "https://mine.example/api/send"\n'
        'website_id = "99999999-2222-3333-4444-555555555555"\n'
    )
    hand = load_site(_site_with(tmp_path / "hand", manual))
    assert umami.forward_settings(hand.config)["endpoint"] == "https://mine.example/api/send"


# ---- the studio --------------------------------------------------------------------------------
def _ready(client) -> str:
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    return _csrf(client.get("/sites/demo/settings").text)


def test_studio_connects_umami_from_a_paste(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _ready(client)
    toml = ws / "sites" / "demo" / "site.toml"
    doc = tomlkit.parse(toml.read_text(encoding="utf-8"))
    doc["custom_html"]["head"] = f"<!-- tracking snippets go here -->\n{CODE}"
    toml.write_text(tomlkit.dumps(doc), encoding="utf-8")

    bad = client.post("/sites/demo/umami", data={"csrf": csrf, "code": "nope"})
    assert "isn&#39;t a tracking code" in bad.text and "nope" in bad.text

    r = client.post("/sites/demo/umami", data={"csrf": csrf, "code": CODE}, follow_redirects=False)
    assert r.status_code == 303 and "moved=1" in r.headers["location"]
    saved = tomlkit.parse(toml.read_text(encoding="utf-8"))
    assert saved["analytics"]["umami"]["website_id"] == WID
    assert WID not in saved["custom_html"]["head"]
    assert "tracking snippets go here" in saved["custom_html"]["head"]

    page = client.get("/sites/demo/settings?saved=1&moved=1").text
    assert "Connected" in page and "taken out there" in page and "demo.example.org" in page
    assert "your Umami dashboard" in client.get("/sites/demo/analytics").text

    client.post("/sites/demo/umami", data={"csrf": csrf, "action": "forward"})
    assert (
        tomlkit.parse(toml.read_text(encoding="utf-8"))["analytics"]["umami"][
            "forward_other_platforms"
        ]
        is False
    )

    client.post("/sites/demo/umami", data={"csrf": csrf, "action": "remove"})
    assert "umami" not in tomlkit.parse(toml.read_text(encoding="utf-8")).get("analytics", {})
    assert client.post("/sites/demo/umami", data={"code": CODE}).status_code == 403


def test_guide_fills_in_the_users_names(studio) -> None:  # noqa: F811
    client, state, ws = studio
    _ready(client)
    assert 'href="/guide"' in client.get("/servers").text
    page = client.get(
        "/guide",
        params={
            "host": "https://cms.example.net/",
            "ip": "203.0.113.7",
            "site": "example.net",
            "proxy": "caddy",
            "port": "8180",
            "gopher": "on",
        },
    ).text
    assert (
        "--admin-hostname cms.example.net --tls-mode behind-proxy --plain-port 8180 --gopher"
        in page
    )
    assert "proxy-snippet --caddy example.net | sudo tee -a /etc/caddy/Caddyfile" in page
    assert "sudo ufw allow 70/tcp" in page and "203.0.113.7" in page
    plain = client.get("/guide", params={"site": "example.net", "proxy": "none"}).text
    assert "--tls-mode acme" in plain and "--gopher" not in plain and "ufw allow 443/tcp" in plain
    assert "--gopher" in client.get("/guide").text  # Gopher is on until the form says otherwise
    hostile = client.get("/guide", params={"site": '"><script>alert(1)</script>'}).text
    assert "<script>alert" not in hostile


def test_server_address_gets_the_api_path(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _ready(client)
    client.post(
        "/servers", data={"csrf": csrf, "id": "vps1", "admin_url": "https://cms.example.net/"}
    )
    client.post(
        "/servers",
        data={"csrf": csrf, "id": "vps2", "admin_url": "https://b.example/_constellation"},
    )
    from constellation.studio.workspace import load_workspace

    urls = {s.id: s.admin_url for s in load_workspace(ws).servers}
    assert urls == {
        "vps1": "https://cms.example.net/_constellation",
        "vps2": "https://b.example/_constellation",
    }


# ---- server install helpers --------------------------------------------------------------------
def test_install_can_turn_on_gopher_and_caddy_snippet(tmp_path: Path) -> None:
    from constellation.server.config import load_server_config
    from constellation.server.install import caddy_snippet, install

    path, _ = install(
        data_dir=tmp_path / "data",
        user="constellation",
        admin_hostname="cms.example",
        tls_mode="behind-proxy",
        write_unit=False,
        plain_port=8180,
        gopher=True,
    )
    assert load_server_config(path).gopher.enabled
    path2, _ = install(
        data_dir=tmp_path / "data2",
        user="constellation",
        admin_hostname="cms.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    assert not load_server_config(path2).gopher.enabled
    snippet = caddy_snippet(["example.org", "www.example.org"], 8180, "cms.example")
    assert "cms.example, example.org, www.example.org {" in snippet
    assert "reverse_proxy 127.0.0.1:8180" in snippet and "max_size 300MB" in snippet
