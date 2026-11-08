"""The ``constellation`` command line."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from constellation import __version__
from constellation.core.models import Site


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="constellation", description="ConstellationCMS")
    parser.add_argument("--version", action="version", version=f"constellation {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build a site into one output tree per platform")
    b.add_argument("site", type=Path, help="path to the site folder (contains site.toml)")
    b.add_argument(
        "--out", type=Path, default=Path("out"), help="output directory (default: ./out)"
    )
    b.add_argument("--platform", action="append", help="limit to a platform (repeatable)")
    b.add_argument("--now", help="build time as ISO 8601 (default: now)")
    b.add_argument("--drafts", action="store_true", help="include drafts and future-dated posts")
    b.add_argument("--strict", action="store_true", help="exit non-zero on error-level warnings")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="load a site and report problems and rendering warnings")
    c.add_argument("site", type=Path)
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.set_defaults(func=cmd_check)

    e = sub.add_parser(
        "export", help="export one platform's build to a folder, over SFTP, or with rsync"
    )
    e.add_argument("site", type=Path)
    e.add_argument("--platform", required=True, choices=["web", "gemini"])
    e.add_argument(
        "--target",
        required=True,
        help="folder:/path | sftp://user@host[:port]/path | rsync:user@host:/path",
    )
    e.add_argument(
        "--layout",
        default="plain",
        choices=["plain", "agate"],
        help="plain release tree, or an Agate content tree (gemini)",
    )
    e.add_argument("--now", help="build time as ISO 8601 (default: now)")
    e.add_argument("--dry-run", action="store_true", help="show the diff and stop")
    e.add_argument("--yes", action="store_true", help="apply without asking")
    e.add_argument("--no-delete", action="store_true", help="never delete remote files")
    e.set_defaults(func=cmd_export)

    n = sub.add_parser("new", help="create a post or page")
    n.add_argument("kind", choices=["post", "page"])
    n.add_argument("site", type=Path)
    n.add_argument("title")
    n.add_argument("--draft", action="store_true")
    n.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)
    result = args.func(args)
    return int(result or 0)


def _load(path: Path) -> Site:
    from constellation.core.content import ContentError, load_site

    try:
        return load_site(path)
    except ContentError as e:
        for p in e.problems:
            print(f"error: {p}", file=sys.stderr)
        raise SystemExit(2) from e


def cmd_build(args: argparse.Namespace) -> int:
    from constellation.core.build.pipeline import build_site, write_build

    site = _load(args.site)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    build = build_site(site, platforms=args.platform, now=now, include_drafts=args.drafts)
    write_build(build, args.out)
    errors = 0
    for pb in build.platforms.values():
        for w in pb.warnings:
            if w.severity == "error":
                errors += 1
                print(f"error [{w.platform}] {w.code}: {w.message}", file=sys.stderr)
    counts = {name: len(pb.files) for name, pb in build.platforms.items()}
    print(
        f"built {args.site.name}: "
        + ", ".join(f"{k} {v} files" for k, v in counts.items())
        + f"; release {build.release_id()}; {len(build.warnings)} warnings"
    )
    return 1 if (errors and args.strict) else 0


def cmd_check(args: argparse.Namespace) -> int:
    from constellation.core.build.pipeline import build_site

    site = _load(args.site)
    build = build_site(site)
    rows = []
    for pb in build.platforms.values():
        for key, ws in pb.item_warnings.items():
            for w in ws:
                rows.append({"item": key, **w.as_dict()})
        for w in pb.warnings:
            if w.platform == "all" and not any(r.get("message") == w.message for r in rows):
                rows.append({"item": "", **w.as_dict()})
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("no warnings")
        for r in rows:
            loc = f"{r['item']}:{r['line']}" if r["item"] else "site"
            print(f"{r['severity']:5} [{r['platform']}] {loc} {r['code']}: {r['message']}")
    return 1 if any(r["severity"] == "error" for r in rows) else 0


def cmd_export(args: argparse.Namespace) -> int:
    from constellation.core.build.pipeline import build_site
    from constellation.publish.export import (
        apply_export,
        export_rsync,
        layout_files,
        open_target,
        plan_export,
    )

    site = _load(args.site)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    build = build_site(site, platforms=[args.platform], now=now)
    errors = [w for w in build.warnings if w.severity == "error"]
    for w in errors:
        print(f"error [{w.platform}] {w.code}: {w.message}", file=sys.stderr)
    if errors:
        print(
            "refusing to export with error-level warnings; fix them or run `constellation check`",
            file=sys.stderr,
        )
        return 1
    files = layout_files(build, args.platform, args.layout, site)
    if args.target.startswith("rsync:"):
        print(
            export_rsync(files, args.target, dry_run=args.dry_run, delete=not args.no_delete),
            end="",
        )
        return 0
    target = open_target(args.target)
    try:
        plan = plan_export(files, target)
        print(f"{target.describe()}: {plan.summary()}")
        for p in plan.added:
            print(f"  + {p}")
        for p in plan.changed:
            print(f"  ~ {p}")
        for p in plan.removed:
            print(f"  - {p}" + ("" if not args.no_delete else " (kept)"))
        if args.dry_run or plan.empty:
            return 0
        if not args.yes:
            answer = input("apply? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted")
                return 1
        apply_export(files, target, plan, delete=not args.no_delete)
        print("done")
        return 0
    finally:
        target.close()


def cmd_new(args: argparse.Namespace) -> int:
    from slugify import slugify

    slug = slugify(args.title)
    today = datetime.now().astimezone()
    if args.kind == "post":
        folder = args.site / "content" / "posts" / f"{today:%Y-%m-%d}-{slug}"
        folder.mkdir(parents=True, exist_ok=False)
        path = folder / "index.md"
        fm = f"---\ntitle: {_yaml_str(args.title)}\ndate: {today:%Y-%m-%dT%H:%M:%S}\nstatus: {'draft' if args.draft else 'published'}\ntags: []\n---\n\n"
    else:
        path = args.site / "content" / "pages" / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = f"---\ntitle: {_yaml_str(args.title)}\n---\n\n"
    path.write_text(fm, encoding="utf-8")
    print(path)
    return 0


def _yaml_str(s: str) -> str:
    return json.dumps(s) if re.search(r"[:#\"'\[\]{}&*!|>%@`]", s) or s.strip() != s else s


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
