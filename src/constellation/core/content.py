"""Load a site folder into models: site.toml, posts, pages, taxonomy descriptors."""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import frontmatter
import yaml
from pydantic import ValidationError
from slugify import slugify

from constellation.core.models import (
    Page,
    PageMeta,
    Post,
    PostMeta,
    Site,
    SiteConfig,
    Term,
    TermMeta,
)

DATED_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>.+)$")
SLUG_RX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ContentError(Exception):
    """Raised when a site cannot be loaded. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def load_config(root: Path) -> SiteConfig:
    path = root / "site.toml"
    if not path.is_file():
        raise ContentError([f"{path}: missing site.toml"])
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        return SiteConfig.model_validate(data)
    except ValidationError as e:
        raise ContentError([f"{path}: {_fmt_validation(e)}"]) from e


def site_tz(name: str, problems: list[str] | None = None) -> tzinfo:
    """Resolve a site's IANA timezone name.

    Falls back to UTC (recording a problem when ``problems`` is given) if the
    name is unknown or no time zone database is available on this machine.
    """
    try:
        return ZoneInfo(name)
    except Exception:
        if problems is not None:
            problems.append(f"site.toml: unknown timezone {name!r}")
        return UTC


def load_site(root: Path, *, site_id: str | None = None) -> Site:
    root = Path(root)
    cfg = load_config(root)
    problems: list[str] = []
    tz = site_tz(cfg.site.timezone, problems)

    posts = _load_posts(root / "content" / "posts", tz, problems)
    pages = _load_pages(root / "content" / "pages", tz, problems)
    _check_unique("post", [p.slug for p in posts], problems)
    _check_unique("page", [p.slug for p in pages], problems)

    site = Site(root=root, id=site_id or root.name, config=cfg, posts=posts, pages=pages)
    for kind, attr in (("tag", "tags"), ("category", "categories"), ("series", "series")):
        terms = _collect_terms(kind, posts, root / "content" / attr, problems)
        setattr(site, attr, terms)

    if cfg.site.author and cfg.author_by_id(cfg.site.author) is None:
        problems.append(f"site.toml: default author {cfg.site.author!r} is not in [[authors]]")
    for p in posts:
        if p.meta.author and cfg.author_by_id(p.meta.author) is None:
            problems.append(f"{p.source}: author {p.meta.author!r} is not in [[authors]]")

    if problems:
        raise ContentError(problems)
    return site


def _load_posts(folder: Path, tz: tzinfo, problems: list[str]) -> list[Post]:
    posts: list[Post] = []
    if not folder.is_dir():
        return posts
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            source = entry / "index.md"
            if not source.is_file():
                continue
            bundle: Path | None = entry
            name = entry.name
        elif entry.suffix == ".md":
            source, bundle, name = entry, None, entry.stem
        else:
            continue
        try:
            post = _load_post(source, bundle, name, tz)
        except ValidationError as e:
            problems.append(f"{source}: {_fmt_validation(e)}")
            continue
        except ContentError as e:
            problems.extend(e.problems)
            continue
        posts.append(post)
    posts.sort(key=lambda p: (p.date, p.slug), reverse=True)
    return posts


def _read_front_matter(source: Path) -> frontmatter.Post:
    """Parse a Markdown file with front matter, turning parse errors into problems."""
    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ContentError([f"{source}: not UTF-8 text; save it as UTF-8"]) from None
    try:
        return frontmatter.loads(text)
    except yaml.YAMLError as e:
        detail = " ".join(str(e).split())
        raise ContentError([f"{source}: front matter is not valid YAML ({detail})"]) from None


def _bundle_media(bundle: Path) -> list[Path]:
    """Every file in a bundle except the Markdown source, including sub-folders."""
    media: list[Path] = []
    for f in sorted(bundle.rglob("*")):
        rel = f.relative_to(bundle)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if f.is_file() and rel.as_posix() != "index.md":
            _check_media_name(f)
            media.append(f)
    return media


def _load_post(source: Path, bundle: Path | None, name: str, tz: tzinfo) -> Post:
    doc = _read_front_matter(source)
    meta = PostMeta.model_validate(doc.metadata)
    m = DATED_NAME.match(name)
    name_slug = m.group("slug") if m else name
    slug = meta.slug or slugify(name_slug) or slugify(meta.title)
    _check_slug(source, slug)
    date = meta.date
    if date is None and m:
        try:
            date = datetime.strptime(m.group("date"), "%Y-%m-%d")
        except ValueError:
            raise ContentError(
                [f"{source}: {m.group('date')!r} in the name is not a real date"]
            ) from None
    if date is None:
        raise ContentError([f"{source}: no date in front matter and none in the folder name"])
    media = _bundle_media(bundle) if bundle is not None else []
    return Post(
        slug=slug,
        source=source,
        bundle=bundle,
        body=doc.content,
        media=media,
        date=_localize(date, tz),
        updated=_localize(meta.updated, tz) if meta.updated else None,
        post_meta=meta,
    )


def _load_pages(folder: Path, tz: tzinfo, problems: list[str]) -> list[Page]:
    pages: list[Page] = []
    if not folder.is_dir():
        return pages
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            source = entry / "index.md"
            if not source.is_file():
                continue
            bundle: Path | None = entry
            name = entry.name
        elif entry.suffix == ".md":
            source, bundle, name = entry, None, entry.stem
        else:
            continue
        try:
            doc = _read_front_matter(source)
            meta = PageMeta.model_validate(doc.metadata)
            slug = meta.slug or slugify(name) or slugify(meta.title)
            _check_slug(source, slug)
            media = _bundle_media(bundle) if bundle is not None else []
            pages.append(
                Page(
                    slug=slug,
                    source=source,
                    bundle=bundle,
                    body=doc.content,
                    media=media,
                    date=_localize(meta.date, tz) if meta.date else EPOCH,
                    updated=_localize(meta.updated, tz) if meta.updated else None,
                    page_meta=meta,
                )
            )
        except ValidationError as e:
            problems.append(f"{source}: {_fmt_validation(e)}")
        except ContentError as e:
            problems.extend(e.problems)
    pages.sort(key=lambda p: (p.meta.menu_order, p.slug))
    return pages


def _collect_terms(
    kind: str, posts: list[Post], folder: Path, problems: list[str]
) -> dict[str, Term]:
    terms: dict[str, Term] = {}
    for post in posts:
        if kind == "tag":
            names = post.meta.tags
        elif kind == "category":
            names = post.meta.categories
        else:
            names = [post.meta.series] if post.meta.series else []
        for name in names:
            slug = slugify(name)
            if not slug:
                problems.append(f"{post.source}: empty {kind} name")
                continue
            term = terms.get(slug)
            if term is None:
                term = Term(kind=kind, slug=slug, meta=TermMeta(), name=name)  # type: ignore[arg-type]
                terms[slug] = term
            term.posts.append(post)
    if folder.is_dir():
        for entry in sorted(folder.glob("*.md")):
            try:
                doc = _read_front_matter(entry)
                meta = TermMeta.model_validate(doc.metadata)
            except ValidationError as e:
                problems.append(f"{entry}: {_fmt_validation(e)}")
                continue
            except ContentError as e:
                problems.extend(e.problems)
                continue
            slug = entry.stem
            term = terms.get(slug)
            if term is None:
                term = Term(kind=kind, slug=slug, meta=meta)  # type: ignore[arg-type]
                terms[slug] = term
            else:
                term.meta = (
                    meta
                    if meta.title
                    else TermMeta(
                        title=term.meta.title, description=meta.description, image=meta.image
                    )
                )
            term.body = doc.content
    if kind == "series":
        for term in terms.values():
            term.posts.sort(
                key=lambda p: (
                    p.meta.series_part if p.meta.series_part is not None else 10**9,
                    p.date,
                    p.slug,
                )
            )
    return dict(sorted(terms.items()))


def _localize(d: datetime, tz: tzinfo) -> datetime:
    if d.tzinfo is None:
        return d.replace(tzinfo=tz)
    return d


def _check_slug(source: Path, slug: str) -> None:
    if not SLUG_RX.match(slug):
        raise ContentError(
            [f"{source}: slug {slug!r} must be lowercase ASCII letters, digits and hyphens"]
        )


def _check_media_name(path: Path) -> None:
    name = path.name
    if " " in name or not name.isascii():
        raise ContentError([f"{path}: media file names must be ASCII without spaces"])


def _check_unique(kind: str, slugs: list[str], problems: list[str]) -> None:
    seen: set[str] = set()
    for s in slugs:
        if s in seen:
            problems.append(f"duplicate {kind} slug {s!r}")
        seen.add(s)


def _fmt_validation(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "front matter"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
