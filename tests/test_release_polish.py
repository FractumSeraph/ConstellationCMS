"""Milestone 10: doctor checks, server backup and restore, the docs-site builder, packaging."""

from __future__ import annotations

import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from constellation.server.backup import backup, restore
from constellation.server.config import load_server_config
from constellation.server.install import install
from constellation.server.releases import Store
from constellation.studio.doctor import format_checks, run_checks
from constellation.studio.state import State

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"


def test_doctor_checks(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".stfolder").mkdir()
    state = State(tmp_path / "state", tmp_path / "cache")
    checks = run_checks(state, ws, running=True)
    by = {c.name: c for c in checks}
    assert by["Workspace"].status == "ok" and by["Sync client"].detail.startswith("Syncthing")
    assert by["Remote mode"].detail.startswith("off")
    text = format_checks(checks)
    assert "ConstellationCMS" in text and "OK" in text
    missing = run_checks(state, tmp_path / "nope", running=True)
    assert next(c for c in missing if c.name == "Workspace").status == "fail"


def test_backup_and_restore_rebuild_releases(tmp_path: Path) -> None:
    from constellation.core.build.pipeline import build_site
    from constellation.core.content import load_site
    from constellation.publish.client import blob_reader, release_manifest

    config_path, _ = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    cfg = load_server_config(config_path)
    site = load_site(DEMO)
    build = build_site(site, now=datetime(2026, 11, 9, tzinfo=UTC))
    store = Store(cfg.data).site("demo")
    rid, missing = store.stage(release_manifest(build, site), max_bytes=10**9)
    reader = blob_reader(build)
    for sha in missing:
        store.put_blob(sha, reader(sha))
    store.commit(rid)
    store.mint_token("studio")
    archive = backup(cfg.data, tmp_path / "backups")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert (
        "server.toml" in names
        and f"sites/demo/releases/{rid}/manifest.json" in names
        and "sites/demo/tokens.json" in names
    )
    assert not any(n.startswith(f"sites/demo/releases/{rid}/web/") for n in names), (
        "release trees are not duplicated"
    )
    assert any(n.startswith("sites/demo/blobs/") for n in names)
    sites = restore(archive, tmp_path / "restored")
    assert sites == ["demo"]
    restored = Store(tmp_path / "restored").site("demo")
    assert restored.current() == rid
    assert (restored.current_dir("web") / "posts" / "the-call" / "index.html").is_file(), (
        "trees rebuilt from blobs"
    )
    assert restored.list_tokens()


def test_cli_doctor_and_backup(tmp_path: Path, capsys) -> None:
    from constellation.cli import main

    rc = main(["doctor", "--workspace", str(tmp_path / "ws"), "--state-dir", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert "Workspace" in out and rc in (0, 1)
    config_path, _ = install(
        data_dir=tmp_path / "data",
        user="x",
        admin_hostname="vps1.example",
        tls_mode="behind-proxy",
        write_unit=False,
    )
    assert (
        main(["server", "backup", "--config", str(config_path), "--out", str(tmp_path / "b")]) == 0
    )
    assert list((tmp_path / "b").glob("constellation-backup-*.tar.gz"))


def test_docs_site_builds(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_docs_site.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "docs")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "docs" / "web" / "plan" / "index.html").is_file()
    assert (tmp_path / "docs" / "gemini" / "themes.gmi").is_file()


def test_version_is_consistent() -> None:
    import tomllib

    from constellation import __version__

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == __version__
