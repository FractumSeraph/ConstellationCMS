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

    s = sub.add_parser("studio", help="run the studio web app")
    s.add_argument(
        "--workspace",
        type=Path,
        help="workspace folder (default: the one remembered in studio.toml, else ./workspace)",
    )
    s.add_argument("--bind", default=None, help="address to bind (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument(
        "--state-dir", type=Path, help="machine-local state folder (default: the OS user data dir)"
    )
    s.set_defaults(func=cmd_studio)

    sv = sub.add_parser("server", help="the VPS server")
    svs = sv.add_subparsers(dest="server_command", required=True)
    r = svs.add_parser("run", help="run the server")
    r.add_argument("--config", type=Path, default=Path("/var/lib/constellation/server.toml"))
    r.set_defaults(func=cmd_server_run)
    i = svs.add_parser(
        "install", help="create the data directory, server.toml, the admin token and a systemd unit"
    )
    i.add_argument("--data-dir", type=Path, default=Path("/var/lib/constellation"))
    i.add_argument("--user", default="constellation")
    i.add_argument(
        "--admin-hostname", required=True, help="the server's own hostname, e.g. vps1.example.org"
    )
    i.add_argument(
        "--tls-mode", choices=["behind-proxy", "external", "acme"], default="behind-proxy"
    )
    i.add_argument("--plain-port", type=int, default=8080, help="local port for behind-proxy mode")
    i.add_argument("--no-unit", action="store_true", help="do not write the systemd unit")
    i.add_argument(
        "--with-studio",
        metavar="HOSTNAME",
        default="",
        help="also run a studio on this server, proxied at HOSTNAME",
    )
    i.add_argument(
        "--studio-workspace",
        type=Path,
        help="workspace folder for that studio (sync it with Syncthing)",
    )
    i.set_defaults(func=cmd_server_install)
    ps = svs.add_parser(
        "proxy-snippet", help="print an nginx/openresty server block for behind-proxy mode"
    )
    ps.add_argument("--config", type=Path, default=Path("/var/lib/constellation/server.toml"))
    ps.add_argument("hostnames", nargs="*")
    ps.set_defaults(func=cmd_server_proxy)
    bk = svs.add_parser(
        "backup",
        help="write a tarball of the data directory (releases are rebuilt from blobs on restore)",
    )
    bk.add_argument("--config", type=Path, default=Path("/var/lib/constellation/server.toml"))
    bk.add_argument("--out", type=Path, default=Path("."))
    bk.set_defaults(func=cmd_server_backup)
    rs = svs.add_parser("restore", help="restore a backup into an empty data directory")
    rs.add_argument("archive", type=Path)
    rs.add_argument("--data-dir", type=Path, required=True)
    rs.set_defaults(func=cmd_server_restore)

    d = sub.add_parser("doctor", help="run the studio self-checks")
    d.add_argument("--workspace", type=Path)
    d.add_argument("--state-dir", type=Path)
    d.set_defaults(func=cmd_doctor)

    pr = svs.add_parser("prune", help="apply release retention to every site")
    pr.add_argument("--config", type=Path, default=Path("/var/lib/constellation/server.toml"))
    pr.set_defaults(func=cmd_server_prune)

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
    from constellation.core.themes import ThemeError, load_theme, workspace_theme_dirs

    try:
        site = load_site(path)
    except ContentError as e:
        for p in e.problems:
            print(f"error: {p}", file=sys.stderr)
        raise SystemExit(2) from e
    except OSError as e:
        print(f"error: cannot read the site: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    try:
        load_theme(site.config.site.theme, search=workspace_theme_dirs(site.root))
    except ThemeError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    return site


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


def cmd_studio(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from constellation.studio.app import create_app
    from constellation.studio.state import State

    state = State(args.state_dir)
    if args.workspace:
        state.settings.workspace = str(Path(args.workspace).expanduser().resolve())
        state.save_settings()
    workspace = Path(state.settings.workspace or Path.cwd() / "workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    if not state.settings.workspace:
        state.settings.workspace = str(workspace.resolve())
        state.save_settings()
    bind = args.bind or state.settings.bind or "127.0.0.1"
    port = args.port or state.settings.port or 8787
    if bind not in ("127.0.0.1", "::1", "localhost") and not state.settings.remote_mode:
        print(
            "refusing to bind a non-loopback address while remote mode is off (enable it in Account first)",
            file=sys.stderr,
        )
        return 2
    app = create_app(state, workspace)
    url = f"http://{'localhost' if bind == '127.0.0.1' else bind}:{port}/"
    print(f"studio: {url}  workspace: {workspace}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=bind, port=port, log_level="warning")
    return 0


def cmd_server_run(args: argparse.Namespace) -> int:
    from constellation.server.app import run

    run(args.config)
    return 0


def cmd_server_install(args: argparse.Namespace) -> int:
    from constellation.server.install import install

    exe = Path(sys.argv[0]).resolve()
    config_path, token = install(
        data_dir=args.data_dir,
        user=args.user,
        admin_hostname=args.admin_hostname,
        tls_mode=args.tls_mode,
        exe=exe,
        write_unit=not args.no_unit,
        plain_port=args.plain_port,
        studio_hostname=args.with_studio,
        studio_workspace=args.studio_workspace,
    )
    if args.with_studio:
        print(
            f"studio on this server: https://{args.with_studio}/ (turn on remote mode with 'trust proxy headers' in its Account page)"
        )
    print(f"wrote {config_path}")
    print("admin token (shown once; paste it into the studio's Servers page):")
    print(f"  {token}")
    if not args.no_unit:
        print(
            "enable the service with: systemctl daemon-reload && systemctl enable --now constellation"
        )
    ports = ["1965 (gemini)"]
    ports += (
        [f"{args.plain_port} (local, behind the proxy)"]
        if args.tls_mode == "behind-proxy"
        else ["80 and 443 (http/https)"]
    )
    print("ports: " + ", ".join(ports))
    return 0


def cmd_server_proxy(args: argparse.Namespace) -> int:
    from constellation.server.config import load_server_config
    from constellation.server.install import proxy_snippet

    cfg = load_server_config(args.config)
    print(proxy_snippet(list(args.hostnames), cfg.https.plain_port, cfg.admin.hostname), end="")
    return 0


def cmd_server_backup(args: argparse.Namespace) -> int:
    from constellation.server.backup import backup
    from constellation.server.config import load_server_config

    cfg = load_server_config(args.config)
    print(backup(cfg.data, args.out))
    return 0


def cmd_server_restore(args: argparse.Namespace) -> int:
    from constellation.server.backup import restore

    sites = restore(args.archive, args.data_dir)
    print(f"restored {len(sites)} sites into {args.data_dir}: {', '.join(sites)}")
    print("review server.toml, then start the service")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from constellation.studio.doctor import format_checks, run_checks
    from constellation.studio.state import State
    from constellation.studio.workspace import load_workspace

    state = State(args.state_dir)
    workspace = Path(args.workspace or state.settings.workspace or Path.cwd() / "workspace")
    servers = load_workspace(workspace).servers if workspace.is_dir() else []
    checks = run_checks(state, workspace, servers=servers)
    print(format_checks(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


def cmd_server_prune(args: argparse.Namespace) -> int:
    from constellation.server.config import load_server_config
    from constellation.server.releases import Store

    cfg = load_server_config(args.config)
    store = Store(cfg.data)
    for sid in store.sites():
        removed = store.site(sid).prune(cfg.limits.keep_releases)
        print(f"{sid}: removed {len(removed)} releases")
    return 0


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
