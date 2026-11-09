from __future__ import annotations

from constellation.core.markdown.document import Ref
from constellation.core.routes import output_file, platform_path
from tests.helpers import CFG, RESOLVER, u


def test_platform_paths() -> None:
    assert platform_path(CFG, "web", "post", "x") == "/posts/x/"
    assert platform_path(CFG, "gemini", "post", "x") == "/posts/x.gmi"
    assert platform_path(CFG, "gemini", "home", "") == "/"
    assert output_file(CFG, "web", "post", "x") == "posts/x/index.html"
    assert output_file(CFG, "gemini", "post", "x") == "posts/x.gmi"
    assert output_file(CFG, "gemini", "home", "") == "index.gmi"
    assert output_file(CFG, "web", "home", "") == "index.html"


def test_resolver_forms() -> None:
    r = RESOLVER.resolve
    assert r("post:foo").kind == "post"
    assert r("page:about#top").fragment == "top"
    assert r("https://example.org/posts/foo/").kind == "post"
    assert r("https://example.org/posts/foo.gmi").target == "foo"
    assert r("https://elsewhere.example/x").kind == "external"
    assert r("/about/").kind == "page" and r("/about/").target == "about"
    assert r("../2026-01-01-foo/index.md").target == "foo"
    assert r("about.md").kind == "page"
    assert r("photo.png", is_image=True).kind == "media"
    assert r("media:/logo.png").target == "/logo.png"
    assert r("#section").kind == "fragment"
    assert r("mailto:a@b.c").kind == "external"


def test_url_for_per_platform() -> None:
    assert u(Ref("post", "foo"), "web") == "/posts/foo/"
    assert u(Ref("post", "foo", "sec"), "gemini") == "/posts/foo.gmi#sec"
    assert u(Ref("media", "pic.png"), "gemini") == "/media/posts/post/pic.png"
    assert u(Ref("media", "/logo.png"), "web") == "/media/logo.png"
    assert u(Ref("tag", "mending"), "gemini") == "/tags/mending.gmi"
    assert u(Ref("external", "https://x.example/p"), "web") == "https://x.example/p"
