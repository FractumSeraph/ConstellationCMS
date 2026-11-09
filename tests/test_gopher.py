"""Milestone 9: the Gopher renderer, build output, and the listener."""

from __future__ import annotations

import asyncio
import shutil
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.core.models import RenderGopher
from constellation.core.render.gopher import (
    GopherRenderer,
    GopherTarget,
    display_width,
    item_for_url,
    wrap,
)
from constellation.core.routes import url_for
from tests.helpers import CFG, parse

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
TARGET = GopherTarget("demo.example.org", 70)


def gmap(src: str) -> str:
    doc = parse(src)
    return (
        GopherRenderer(
            RenderGopher(), lambda r: url_for(r, "gopher", CFG, item_slug="post"), TARGET
        )
        .render(doc)
        .text
    )


def gtxt(src: str) -> str:
    doc = parse(src)
    return (
        GopherRenderer(
            RenderGopher(), lambda r: url_for(r, "gopher", CFG, item_slug="post"), TARGET
        )
        .render_text(doc)
        .text
    )


def test_wrap_and_width() -> None:
    assert display_width("abc") == 3 and display_width("日本") == 4
    long = "word " * 30
    lines = wrap(long.strip(), 67)
    assert all(display_width(ln) <= 67 for ln in lines) and len(lines) == 3
    assert wrap("supercalifragilistic", 5) == ["supercalifragilistic"], (
        "a word longer than the width is not broken"
    )
    assert wrap("a b", 10, indent="  ", first="* ") == ["* a b"]


def test_items_and_links_after_block() -> None:
    out = gmap("Read [the docs](https://docs.example/x) now.\n\nNext.\n")
    lines = out.split("\n")
    assert lines[0] == "iRead the docs now.\tfake\tdemo.example.org\t70"
    assert lines[1] == "hthe docs\tURL:https://docs.example/x\tdemo.example.org\t70"
    assert item_for_url("/posts/foo/", "Foo", TARGET) == "1Foo\t/posts/foo\tdemo.example.org\t70"
    assert item_for_url("/posts/foo.txt", "Foo", TARGET).startswith("0Foo\t")
    assert item_for_url("/media/posts/post/pic.png", "pic", TARGET).startswith("Ipic\t")
    assert (
        gmap("See [other](post:other).\n").split("\n")[1]
        == "1other\t/posts/other\tdemo.example.org\t70"
    )


def test_headings_lists_quotes_tables_images_in_text_mode() -> None:
    src = "## Section\n\n### Sub\n\n- one\n- two\n  - nested\n\n> a quote\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n![A cat](cat.png)\n\nLink [here](https://h.example).\n"
    txt = gtxt(src)
    assert "SECTION" in txt and "Sub\n---" in txt
    assert "  * one" in txt and "    * nested" in txt
    assert '    "a quote"' in txt
    assert "  * A: 1" in txt and "    B: 2" in txt
    assert "[Image: A cat]" in txt
    assert "here[2]" in txt and "Links:" in txt and "[2] here: https://h.example" in txt
    assert "[1] A cat: /media/posts/post/cat.png" in txt


def test_dot_stuffing_and_wide_preformatted_warning() -> None:
    txt = gtxt(". starts with a dot\n")
    assert txt.startswith(".. starts")
    doc = parse("```\n" + "x" * 90 + "\n```\n")
    res = GopherRenderer(RenderGopher(), lambda r: "/", TARGET).render(doc)
    assert any(w.code == "PRE-TOO-WIDE" for w in res.warnings)


WORDS = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Zs")), min_size=1, max_size=10
    ),
    min_size=1,
    max_size=12,
).map(" ".join)
DOC = st.lists(
    st.one_of(
        WORDS,
        WORDS.map(lambda w: "## " + w),
        WORDS.map(lambda w: "- " + w),
        WORDS.map(lambda w: "> " + w),
        WORDS.map(lambda w: f"[{w}](https://e.example/)"),
    ),
    min_size=1,
    max_size=6,
).map(lambda bs: "\n\n".join(bs) + "\n")


@settings(max_examples=80, deadline=None)
@given(DOC)
def test_gophermap_lines_are_well_formed(src: str) -> None:
    out = gmap(src)
    for line in out.split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        assert len(parts) == 4, line
        assert parts[0][0] in "ih01I9", line
        assert display_width(parts[0][1:]) <= 68, line
        assert "\t" not in parts[0] and "\r" not in line


def test_build_writes_gophermaps_and_text(tmp_path: Path) -> None:
    shutil.copytree(DEMO, tmp_path / "site")
    toml = tmp_path / "site" / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            "[platforms.gopher]\nenabled = false", "[platforms.gopher]\nenabled = true"
        )
        if "[platforms.gopher]" in toml.read_text(encoding="utf-8")
        else toml.read_text(encoding="utf-8")
        + '\n[platforms.gopher]\nenabled = true\nbase_url = "gopher://demo.example.org"\nhostnames = ["demo.example.org"]\nport = 70\n',
        encoding="utf-8",
    )
    build = build_site(load_site(tmp_path / "site"), now=NOW)
    files = build.platforms["gopher"].files
    for path in (
        "gophermap",
        "posts/the-call/gophermap",
        "posts/the-call.txt",
        "tags/gophermap",
        "tags/foundations/gophermap",
        "archive/gophermap",
        "about/gophermap",
        "media/posts/rebuilding-the-workbench/workbench.png",
    ):
        assert path in files, path
    home = files["gophermap"].data.decode()
    assert home.startswith("iDemo Constellation\tfake\tdemo.example.org\t70\r\n")
    assert (
        "12026-11-08 Rebuilding the Workbench\t/posts/rebuilding-the-workbench\tdemo.example.org\t70\r\n"
        in home
    )
    post = files["posts/the-call/gophermap"].data.decode()
    assert "0Plain text version\t/posts/the-call.txt\t" in post
    assert "hRead this post on the web\tURL:https://demo.example.org/posts/the-call/\t" in post
    assert all(len(ln.split("\t")) == 4 for ln in post.split("\r\n") if ln)
    txt = files["posts/the-call.txt"].data.decode()
    assert txt.startswith("THE CALL\r\n========\r\n") and "\n" not in txt.replace("\r\n", "")
    assert "robots.txt" not in files and "feed.xml" not in files


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _req(port: int, selector: str) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(selector.encode() + b"\r\n")
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return data


def test_gopher_listener(tmp_path: Path) -> None:
    from constellation.server.gopher import GopherServer

    shutil.copytree(DEMO, tmp_path / "site")
    toml = tmp_path / "site" / "site.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[platforms.gopher]\nenabled = true\nbase_url = "gopher://demo.example.org"\nhostnames = ["demo.example.org"]\n',
        encoding="utf-8",
    )
    build = build_site(load_site(tmp_path / "site"), platforms=["gopher"], now=NOW)
    from constellation.core.build.pipeline import write_build

    write_build(build, tmp_path / "out")
    root = tmp_path / "out" / "gopher"
    port = _free_port()
    events: list[dict] = []
    server = GopherServer(
        lambda: ("demo", root),
        host="demo.example.org",
        port=port,
        web_url="https://demo.example.org/",
        on_event=events.append,
    )
    loop = asyncio.new_event_loop()
    started = threading.Event()
    stop = False

    async def run() -> None:
        await server.start(["127.0.0.1"], port)
        started.set()
        while not stop:
            await asyncio.sleep(0.05)
        await server.stop()

    thread = threading.Thread(target=lambda: loop.run_until_complete(run()), daemon=True)
    thread.start()
    assert started.wait(10)
    try:
        home = _req(port, "")
        assert home.startswith(b"iDemo Constellation\tfake\tdemo.example.org\t") and home.endswith(
            b"\r\n.\r\n"
        )
        menu = _req(port, "/posts/the-call")
        assert b"0Plain text version\t/posts/the-call.txt\t" in menu and menu.endswith(b".\r\n")
        assert _req(port, "/posts/the-call/") == menu
        txt = _req(port, "/posts/the-call.txt")
        assert txt.startswith(b"THE CALL\r\n") and txt.endswith(b"\r\n.\r\n")
        missing = _req(port, "/nope")
        assert missing.startswith(b"3Not found\t") and missing.endswith(b".\r\n")
        assert _req(port, "/../../etc/passwd").startswith(b"3")
        png = _req(port, "/media/posts/rebuilding-the-workbench/workbench.png")
        assert png.startswith(b"\x89PNG") and not png.endswith(b".\r\n"), "binaries are raw"
        html = _req(port, "GET / HTTP/1.1")
        assert html.startswith(b"HTTP/1.0 200") and b"https://demo.example.org/" in html
        redirect = _req(port, "URL:https://x.example/")
        assert b"url=https://x.example/" in redirect
    finally:
        stop = True
        thread.join(timeout=10)
    assert any(e["status"] == 20 and e["site"] == "demo" for e in events) and any(
        e["status"] == 51 for e in events
    )
