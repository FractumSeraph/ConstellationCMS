"""Site configuration and content models (validated with pydantic)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["web", "gemini", "gopher"]
PLATFORMS: tuple[Platform, ...] = ("web", "gemini", "gopher")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Author(Strict):
    id: str
    name: str
    bio: str = ""
    url: str = ""
    # A picture for the author page: a file in the site's media folder ("/me.png") or a URL.
    avatar: str = ""


class SiteInfo(Strict):
    title: str
    tagline: str = ""
    language: str = "en"
    timezone: str = "UTC"
    theme: str = "base"
    author: str = ""


class PlatformConfig(Strict):
    enabled: bool = True
    base_url: str = ""
    hostnames: list[str] = Field(default_factory=list)
    pagination: int = 10
    feeds: list[str] = Field(default_factory=list)
    port: int | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class UrlPatterns(Strict):
    post: str = "/posts/{slug}/"
    page: str = "/{slug}/"
    tag: str = "/tags/{slug}/"
    category: str = "/categories/{slug}/"
    series: str = "/series/{slug}/"
    author: str = "/authors/{slug}/"
    archive: str = "/archive/{year}/"


class MenuItem(Strict):
    label: str
    ref: str | None = None
    url: str | None = None
    children: list[MenuItem] = Field(default_factory=list)


class Menu(Strict):
    items: list[MenuItem] = Field(default_factory=list)


class SocialLink(Strict):
    label: str
    url: str


class Footer(Strict):
    text: str = ""
    social: list[SocialLink] = Field(default_factory=list)


class Seo(Strict):
    default_description: str = ""
    twitter: str = ""
    image: str = ""


class CustomHtml(Strict):
    head: str = ""
    body_start: str = ""
    body_end: str = ""


class RenderGemini(Strict):
    inline_markup: Literal["markdown", "strip"] = "markdown"
    link_placement: Literal["after-block", "per-item", "at-end"] = "after-block"
    link_markers: Literal["none", "numbers", "letters"] = "none"
    dedupe_links: Literal["block", "document", "off"] = "block"
    image_notice: str = "[Image, web only. Alt text: {alt}]"
    image_link: bool = True
    heading_overflow: Literal["text", "promote", "upper"] = "text"
    nested_lists: Literal["indent", "flatten", "sentence"] = "indent"
    ordered_lists: Literal["numbered-text", "bullet-numbered"] = "numbered-text"
    tables: Literal["pre", "records", "auto"] = "auto"
    hr: str = "~~~~"
    footnotes: Literal["end-section", "inline-parens"] = "end-section"
    notes_heading: str = "### Notes"
    embed_notice: str = "[Embedded content, web only]"
    lang: str = "en"
    long_cell_chars: int = 40
    wide_table_cols: int = 80


class RenderGopher(Strict):
    format: Literal["gophermap", "text", "both"] = "gophermap"
    width_map: int = 67
    width_text: int = 70
    ascii_fold: bool = False


class RenderConfig(Strict):
    # Summaries in post lists and feeds: 0 = the first paragraph (up to 240 characters);
    # a number = that many words, read across paragraphs (as Publii and many blogs do).
    summary_words: int = Field(default=0, ge=0, le=500)
    gemini: RenderGemini = Field(default_factory=RenderGemini)
    gopher: RenderGopher = Field(default_factory=RenderGopher)


SHARE_NETWORKS = (
    "mastodon",
    "bluesky",
    "facebook",
    "x",
    "reddit",
    "linkedin",
    "hackernews",
    "telegram",
    "whatsapp",
    "email",
    "copy",
)


class Features(Strict):
    """Reader-facing extras on the web version (and, where it makes sense, Gemini and Gopher)."""

    # A table of contents above posts with at least ``toc_min_headings`` headings. A post's
    # ``toc: true`` or ``toc: false`` front matter overrides this either way.
    toc: bool = False
    toc_min_headings: int = Field(default=3, ge=1, le=50)
    # Posts sharing the most tags, shown under each post; 0 turns it off.
    related_posts: int = Field(default=3, ge=0, le=12)
    # Click an image to see it full size; galleries always open in the viewer.
    lightbox: bool = True
    # A search page at /search/ that works without a server (it reads search.json).
    search: bool = True
    # Plain share links (no scripts or trackers), in this order.
    share: list[str] = Field(default_factory=list)
    # Pin posts marked ``featured: true`` to the top of the first home page.
    pin_featured: bool = True

    @field_validator("share")
    @classmethod
    def _known_networks(cls, v: list[str]) -> list[str]:
        unknown = [n for n in v if n not in SHARE_NETWORKS]
        if unknown:
            raise ValueError(
                f"unknown share network {unknown[0]!r}; choose from {', '.join(SHARE_NETWORKS)}"
            )
        return list(dict.fromkeys(v))


class Comments(Strict):
    """Comments under posts on the web. Pages and lists never show them."""

    provider: Literal["", "giscus", "disqus", "mastodon", "custom"] = ""
    # giscus (https://giscus.app): copy these from the configuration it generates.
    repo: str = ""
    repo_id: str = ""
    category: str = ""
    category_id: str = ""
    mapping: str = "pathname"
    giscus_theme: str = "preferred_color_scheme"
    # Disqus: the site's shortname.
    shortname: str = ""
    # Mastodon: each post names its toot with ``mastodon: https://instance/@you/123``.
    # custom: any embed code, shown under every post.
    html: str = ""

    @field_validator("repo")
    @classmethod
    def _repo(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", v):
            raise ValueError("giscus repo must look like owner/repository")
        return v

    @field_validator("shortname")
    @classmethod
    def _shortname(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9-]+", v):
            raise ValueError("a Disqus shortname is letters, digits and dashes")
        return v


class PluginsConfig(Strict):
    """Python plugins (see ``constellation.core.plugins``) turned on for this site."""

    enabled: list[str] = Field(default_factory=list)
    # Settings per plugin: [plugins.settings.<name>] key = value
    settings: dict[str, dict[str, object]] = Field(default_factory=dict)


class CalendarConfig(Strict):
    display: list[str] = Field(default_factory=lambda: ["gregorian"])
    numerals: str = "arabic"
    numerals_text_fallback: Literal["arabic", "words"] = "arabic"


class UmamiForward(Strict):
    enabled: bool = False
    endpoint: str = ""
    website_id: str = ""


class UmamiSite(Strict):
    """Set from the tracking code pasted in the studio; see ``constellation.core.umami``."""

    script_url: str = ""
    website_id: str = ""
    host_url: str = ""
    # Also send Gemini and Gopher page views (which can't run scripts) from the server.
    forward_other_platforms: bool = True


class AnalyticsConfig(Strict):
    umami: UmamiSite = Field(default_factory=UmamiSite)
    forward_to_umami: UmamiForward = Field(default_factory=UmamiForward)


class Redirect(Strict):
    from_path: str = Field(alias="from")
    to: str
    status: int = 301

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExportSpec(Strict):
    name: str
    platform: Platform = "web"
    target: str
    layout: Literal["plain", "agate"] = "plain"
    # Also update this target whenever the site is published to its server.
    with_publish: bool = False


class SiteConfig(Strict):
    version: int = 1
    site: SiteInfo
    exports: list[ExportSpec] = Field(default_factory=list)
    redirects: list[Redirect] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    authors: list[Author] = Field(default_factory=list)
    # With no [platforms] table at all, a site is a plain web site.
    platforms: dict[str, PlatformConfig] = Field(
        default_factory=lambda: {"web": PlatformConfig(enabled=True)}
    )
    urls: UrlPatterns = Field(default_factory=UrlPatterns)
    menu: Menu = Field(default_factory=Menu)
    footer: Footer = Field(default_factory=Footer)
    seo: Seo = Field(default_factory=Seo)
    custom_html: CustomHtml = Field(default_factory=CustomHtml)
    render: RenderConfig = Field(default_factory=RenderConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    features: Features = Field(default_factory=Features)
    comments: Comments = Field(default_factory=Comments)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    theme: dict[str, object] = Field(default_factory=dict)

    def enabled_platforms(self) -> list[str]:
        return [p for p in PLATFORMS if p in self.platforms and self.platforms[p].enabled]

    def platform(self, name: str) -> PlatformConfig:
        return self.platforms.get(name, PlatformConfig(enabled=False))

    def author_by_id(self, author_id: str) -> Author | None:
        for a in self.authors:
            if a.id == author_id:
                return a
        return None


class SeoMeta(Strict):
    title: str = ""
    description: str = ""
    canonical: str = ""
    noindex: bool = False


class PlatformMeta(Strict):
    summary: str = ""


class ItemMeta(BaseModel):
    """Front matter shared by posts and pages. Unknown keys are kept for themes."""

    model_config = ConfigDict(extra="allow")

    title: str
    slug: str = ""
    date: datetime | None = None
    updated: datetime | None = None
    status: Literal["draft", "published", "hidden"] = "published"
    author: str = ""
    summary: str = ""
    image: str = ""
    image_alt: str = ""
    seo: SeoMeta = Field(default_factory=SeoMeta)
    platforms: dict[str, PlatformMeta] = Field(default_factory=dict)
    template: str = ""
    # None follows the site's [features] toc setting.
    toc: bool | None = None


class PostMeta(ItemMeta):
    tags: list[str] = Field(default_factory=list)
    # Pinned to the top of the home page and marked as featured in lists.
    featured: bool = False
    # Show comments under this post (when the site has a comments provider).
    comments: bool = True
    # The toot whose replies are this post's comments, for the Mastodon provider.
    mastodon: str = ""
    categories: list[str] = Field(default_factory=list)
    series: str = ""
    series_part: int | None = None


class PageMeta(ItemMeta):
    menu_order: int = 0


class ContentItem(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: Literal["post", "page"]
    slug: str
    source: Path
    bundle: Path | None
    body: str
    media: list[Path] = Field(default_factory=list)
    date: datetime
    updated: datetime | None = None

    @property
    def is_published(self) -> bool:
        return self.meta.status == "published"

    def media_name(self, path: Path) -> str:
        """A media file's name relative to its bundle, with forward slashes."""
        if self.bundle is None:
            return path.name
        return path.relative_to(self.bundle).as_posix()

    @property
    def meta(self) -> ItemMeta:  # pragma: no cover - overridden
        raise NotImplementedError


class Post(ContentItem):
    kind: Literal["post", "page"] = "post"
    post_meta: PostMeta

    @property
    def meta(self) -> PostMeta:
        return self.post_meta


class Page(ContentItem):
    kind: Literal["post", "page"] = "page"
    page_meta: PageMeta

    @property
    def meta(self) -> PageMeta:
        return self.page_meta


class TermMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = ""
    description: str = ""
    image: str = ""


class Term(BaseModel):
    """A tag, category or series, with the posts that carry it."""

    kind: Literal["tag", "category", "series", "author"]
    slug: str
    meta: TermMeta
    name: str = ""
    body: str = ""
    posts: list[Post] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return self.meta.title or self.name or self.slug.replace("-", " ").title()


class Site(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    id: str
    config: SiteConfig
    posts: list[Post] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    tags: dict[str, Term] = Field(default_factory=dict)
    categories: dict[str, Term] = Field(default_factory=dict)
    series: dict[str, Term] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)

    def post_by_slug(self, slug: str) -> Post | None:
        for p in self.posts:
            if p.slug == slug:
                return p
        return None

    def page_by_slug(self, slug: str) -> Page | None:
        for p in self.pages:
            if p.slug == slug:
                return p
        return None

    def published_posts(self, now: datetime) -> list[Post]:
        """Posts that are listed: published and not in the future, newest first."""
        return [p for p in self.posts if p.meta.status == "published" and p.date <= now]

    def visible_posts(self, now: datetime) -> list[Post]:
        """Posts that are built (published or hidden), newest first."""
        return [p for p in self.posts if p.meta.status in ("published", "hidden") and p.date <= now]
