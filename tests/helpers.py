from __future__ import annotations

from constellation.core.markdown.document import Document, Ref
from constellation.core.markdown.parse import parse_markdown
from constellation.core.models import PlatformConfig, RenderGemini, SiteConfig, SiteInfo
from constellation.core.render.base import RenderResult
from constellation.core.render.gemtext import GemtextRenderer
from constellation.core.render.html import HtmlRenderer
from constellation.core.routes import RefResolver, url_for

CFG = SiteConfig(
    site=SiteInfo(title="Test"),
    platforms={
        "web": PlatformConfig(base_url="https://example.org", hostnames=["example.org"]),
        "gemini": PlatformConfig(base_url="gemini://example.org", hostnames=["example.org"]),
    },
)
RESOLVER = RefResolver(CFG, post_slugs={"other", "foo", "zed", "alpha"}, page_slugs={"about"})


def parse(src: str) -> Document:
    return parse_markdown(src, resolve=lambda h, i: RESOLVER.resolve(h, is_image=i))


def gem(src: str, cfg: RenderGemini | None = None) -> RenderResult:
    doc = parse(src)
    return GemtextRenderer(cfg or RenderGemini(), lambda r: url_for(r, "gemini", CFG, item_slug="post")).render(doc)


def html(src: str) -> RenderResult:
    doc = parse(src)
    return HtmlRenderer(lambda r: url_for(r, "web", CFG, item_slug="post")).render(doc)


def u(ref: Ref, platform: str) -> str:
    return url_for(ref, platform, CFG, item_slug="post")
