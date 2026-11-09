"""Publishing the web version to GitHub Pages (or any git repository) as an export target.

The git tests use a bare repository in a temporary folder as the "remote", so they need git
but no network and no GitHub account.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomlkit

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.export import (
    GitTarget,
    apply_export,
    layout_files,
    open_target,
    pages_hostname,
    parse_git_spec,
    plan_export,
    redirect_pages,
    remove_tree,
)
from constellation.studio.jobs import Job
from tests.test_studio import _setup, studio  # noqa: F401  (pytest fixture)

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-site"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _site(tmp_path: Path, **edits: object) -> Path:
    root = tmp_path / "site"
    shutil.copytree(DEMO, root)
    doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
    for key, value in edits.items():
        doc[key] = value
    (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    return root


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _remote(tmp_path: Path, files: dict[str, str] | None = None, branch: str = "main") -> Path:
    """A bare repository, optionally holding one commit with ``files`` (like a Publii site)."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(bare, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    if files:
        seed = tmp_path / "seed"
        subprocess.run(["git", "init", "-q", str(seed)], check=True)
        _git(seed, "checkout", "-q", "-b", branch)
        for name, text in files.items():
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(text, encoding="utf-8")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "Publii")
        _git(seed, "push", "-q", bare.as_uri(), f"HEAD:refs/heads/{branch}")
    return bare


def _checkout(tmp_path: Path, bare: Path, branch: str = "main") -> Path:
    out = tmp_path / f"check-{branch}"
    if out.exists():
        remove_tree(out)
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "clone",
            "-q",
            "--branch",
            branch,
            bare.as_uri(),
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


# ---- what gets written -------------------------------------------------------------------------
def test_redirects_become_forwarding_pages(tmp_path: Path) -> None:
    root = _site(
        tmp_path,
        redirects=[
            {"from": "/authors/seraph/page/2/", "to": "/authors/seraph/"},
            {"from": "/old.html", "to": "https://example.org/new?a=1&b=2#x"},
            {"from": "/feed", "to": "/feed.xml"},
            {"from": "//evil.example/", "to": "/"},  # not a path on this site: skipped
        ],
    )
    pages = redirect_pages(load_site(root))
    assert set(pages) == {"authors/seraph/page/2/index.html", "old.html", "feed/index.html"}
    page = pages["old.html"].decode()
    assert 'content="0; url=https://example.org/new?a=1&amp;b=2#x"' in page
    assert 'rel="canonical" href="https://example.org/new?a=1&amp;b=2#x"' in page
    assert 'location.replace("https://example.org/new?a=1&b=2#x" + location.hash)' in page


def test_forwarding_page_cannot_break_out_of_the_script(tmp_path: Path) -> None:
    root = _site(tmp_path, redirects=[{"from": "/x/", "to": "/</script><script>alert(1)//"}])
    page = redirect_pages(load_site(root))["x/index.html"].decode()
    assert "</script><script>" not in page


def test_pages_layout_adds_cname_and_nojekyll(tmp_path: Path) -> None:
    root = _site(tmp_path, redirects=[{"from": "/gone/", "to": "/"}])
    site = load_site(root)
    build = build_site(site, platforms=["web"], now=NOW)
    plain = layout_files(build, "web", "plain", site)
    assert "gone/index.html" in plain.files  # every static web export gets them
    assert "CNAME" not in plain.files and ".nojekyll" not in plain.files
    pages = layout_files(build, "web", "plain", site, pages=True)
    assert pages.files["CNAME"][0] == b"demo.example.org\n"
    assert pages.files[".nojekyll"][0] == b""
    assert set(pages.manifest) == set(pages.files)
    with pytest.raises(ValueError, match="web platform only"):
        layout_files(
            build_site(site, platforms=["gemini"], now=NOW), "gemini", "plain", site, pages=True
        )


def test_redirect_never_replaces_a_real_page(tmp_path: Path) -> None:
    root = _site(tmp_path, redirects=[{"from": "/", "to": "/elsewhere/"}])
    site = load_site(root)
    build = build_site(site, platforms=["web"], now=NOW)
    files = layout_files(build, "web", "plain", site)
    assert b"has moved" not in (files.files["index.html"][0] or b"")


def test_no_cname_for_a_github_io_address(tmp_path: Path) -> None:
    root = _site(tmp_path)
    doc = tomlkit.parse((root / "site.toml").read_text(encoding="utf-8"))
    doc["platforms"]["web"]["base_url"] = "https://someone.github.io/site"
    doc["platforms"]["web"]["hostnames"] = []
    (root / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
    assert pages_hostname(load_site(root)) == ""


@pytest.mark.parametrize(
    ("spec", "url", "branch"),
    [
        (
            "github:Some-Owner/some.site",
            "https://github.com/Some-Owner/some.site.git",
            "",
        ),
        ("github:me/me.github.io#gh-pages", "https://github.com/me/me.github.io.git", "gh-pages"),
        (
            "git:https://git.example.org/me/site.git#pages",
            "https://git.example.org/me/site.git",
            "pages",
        ),
    ],
)
def test_parses_git_targets(spec: str, url: str, branch: str) -> None:
    assert parse_git_spec(spec)[:2] == (url, branch)


@pytest.mark.parametrize(
    "spec",
    [
        "github:owner",
        "github:owner/repo/extra",
        "github:-owner/repo",
        "github:owner/repo#bad branch",
        "git:--upload-pack=evil",
        "git:https://x.example/r.git;rm",
    ],
)
def test_rejects_bad_git_targets(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_git_spec(spec)


# ---- the git target ----------------------------------------------------------------------------
@needs_git
def test_first_publish_replaces_another_tools_files_and_keeps_the_readme(tmp_path: Path) -> None:
    bare = _remote(
        tmp_path,
        {
            "index.html": "<p>Publii</p>",
            "old-post/index.html": "<p>old</p>",
            "README.md": "# My site",
            "CNAME": "demo.example.org\n",
            ".github/workflows/x.yml": "on: push\n",
        },
    )
    site = load_site(_site(tmp_path))
    files = layout_files(
        build_site(site, platforms=["web"], now=NOW), "web", "plain", site, pages=True
    )
    target = GitTarget(f"git:{bare.as_uri()}", cache_dir=tmp_path / "cache")
    assert target.branch == "main"
    plan = plan_export(files, target)
    assert "old-post/index.html" in plan.removed
    assert "index.html" in plan.changed
    assert not {"README.md", "CNAME", ".github/workflows/x.yml"} & set(plan.removed)
    apply_export(files, target, plan)

    out = _checkout(tmp_path, bare)
    assert not (out / "old-post").exists()
    assert (out / "README.md").read_text(encoding="utf-8") == "# My site"
    assert (out / ".github" / "workflows" / "x.yml").is_file()
    assert (out / ".nojekyll").is_file()
    assert (out / "index.html").read_bytes() == files.files["index.html"][0]
    assert not (out / ".constellation-export.json").exists()  # nothing extra on the public site
    log = _git(out, "log", "--format=%s")
    assert log.splitlines() == ["Publish with ConstellationCMS", "Publii"]

    # The next export finds nothing to do and makes no empty commit.
    again = GitTarget(f"git:{bare.as_uri()}", cache_dir=tmp_path / "cache")
    plan2 = plan_export(files, again)
    assert plan2.empty, plan2.summary()
    apply_export(files, again, plan2)
    assert len(_git(_checkout(tmp_path, bare), "log", "--format=%s").splitlines()) == 2


@needs_git
def test_starts_from_what_is_published_not_from_the_cache(tmp_path: Path) -> None:
    bare = _remote(tmp_path, {"index.html": "old"})
    site = load_site(_site(tmp_path))
    files = layout_files(
        build_site(site, platforms=["web"], now=NOW), "web", "plain", site, pages=True
    )
    first = GitTarget(f"git:{bare.as_uri()}", cache_dir=tmp_path / "cache")
    apply_export(files, first, plan_export(files, first))
    # Someone deletes a page on GitHub, and a stray file is left in the cached copy.
    other = _checkout(tmp_path, bare)
    _git(other, "rm", "-q", "feed.xml")
    _git(other, "commit", "-q", "-m", "remove feed")
    _git(other, "push", "-q", "origin", "HEAD:main")
    (first.workdir / "stray.txt").write_text("x", encoding="utf-8")

    target = GitTarget(f"git:{bare.as_uri()}", cache_dir=tmp_path / "cache")
    assert not (target.workdir / "stray.txt").exists()
    plan = plan_export(files, target)
    assert plan.added == ["feed.xml"] and not plan.changed and not plan.removed
    apply_export(files, target, plan)
    assert (_checkout(tmp_path, bare) / "feed.xml").is_file()


@needs_git
def test_empty_repository_and_a_new_branch(tmp_path: Path) -> None:
    site = load_site(_site(tmp_path))
    files = layout_files(
        build_site(site, platforms=["web"], now=NOW), "web", "plain", site, pages=True
    )

    empty = _remote(tmp_path)
    target = GitTarget(f"git:{empty.as_uri()}", cache_dir=tmp_path / "cache")
    plan = plan_export(files, target)
    assert not plan.removed and len(plan.added) == len(files.files)
    apply_export(files, target, plan)
    assert (_checkout(tmp_path, empty, "main") / "index.html").is_file()

    # A branch that does not exist yet starts empty, without the default branch's files.
    pages = GitTarget(f"git:{empty.as_uri()}#gh-pages", cache_dir=tmp_path / "cache2")
    assert pages.branch == "gh-pages"
    plan = plan_export(files, pages)
    assert len(plan.added) == len(files.files) and not plan.removed
    apply_export(files, pages, plan)
    assert (_checkout(tmp_path, empty, "gh-pages") / "CNAME").is_file()


@needs_git
def test_refuses_to_write_inside_dot_git(tmp_path: Path) -> None:
    target = GitTarget(f"git:{_remote(tmp_path).as_uri()}", cache_dir=tmp_path / "cache")
    with pytest.raises(ValueError, match=".git"):
        target.write(".git/config", b"x", None)
    with pytest.raises(ValueError, match=".git"):
        target.write(".GIT/hooks/pre-commit", b"x", None)


def test_open_target_reports_a_missing_repository_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("CONSTELLATION_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(RuntimeError, match="git clone failed"):
        open_target(f"git:{(tmp_path / 'missing.git').as_uri()}")


# ---- the studio ----------------------------------------------------------------------------------
def test_studio_adds_a_github_target_linked_to_publish(studio) -> None:  # noqa: F811
    client, state, ws = studio
    csrf = _setup(client)
    form = {
        "csrf": csrf,
        "name": "pages",
        "platform": "web",
        "target": "github:me/site",
        "layout": "plain",
    }
    r = client.post(
        "/sites/demo/exports", data={**form, "with_publish": "1"}, follow_redirects=False
    )
    assert r.status_code == 303
    doc = tomlkit.parse((ws / "sites" / "demo" / "site.toml").read_text(encoding="utf-8"))
    entry = doc["exports"][-1]
    assert entry["target"] == "github:me/site" and entry["with_publish"] is True
    assert load_site(ws / "sites" / "demo").config.exports[-1].with_publish
    page = client.get("/sites/demo/publish").text
    assert "also updated by the server" in page and "github:owner/repository" in page

    bad = client.post("/sites/demo/exports", data={**form, "name": "g2", "platform": "gemini"})
    assert bad.status_code == 400 and "web platform only" in bad.text
    bad = client.post("/sites/demo/exports", data={**form, "name": "g3", "target": "github:me"})
    assert bad.status_code == 400


def test_guide_explains_github_pages(studio) -> None:  # noqa: F811
    client, state, ws = studio
    _setup(client)
    page = client.get("/guide", params={"site": "fractum.example"}).text
    assert 'id="github-pages"' in page
    assert "gh auth setup-git" in page and "gemini.fractum.example" in page


@needs_git
def test_server_publish_also_runs_linked_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from constellation.studio.servers import _run_linked_exports

    monkeypatch.setenv("CONSTELLATION_CACHE_DIR", str(tmp_path / "cache"))
    bare = _remote(tmp_path)
    ws = tmp_path / "ws"
    root = _site(
        ws,
        exports=[
            {
                "name": "pages",
                "platform": "web",
                "target": f"git:{bare.as_uri()}",
                "with_publish": True,
            },
            {"name": "manual", "platform": "web", "target": f"folder:{tmp_path / 'manual'}"},
        ],
    )
    site = load_site(root)
    job = Job(id="j", kind="publish", site="demo")
    _run_linked_exports(job, root, "site", site, apply=False)
    assert not (_checkout_or_none(tmp_path, bare))  # a preview pushes nothing
    _run_linked_exports(job, root, "site", site, apply=True)
    assert (_checkout(tmp_path, bare) / "index.html").is_file()
    assert not (tmp_path / "manual").exists()  # only targets marked with_publish
    assert any("export pages" in line for line in job.log)

    broken_root = _site(
        tmp_path / "b",
        exports=[
            {
                "name": "pages",
                "platform": "web",
                "target": f"git:{(tmp_path / 'nope.git').as_uri()}",
                "with_publish": True,
            }
        ],
    )
    broken = load_site(broken_root)
    with pytest.raises(
        RuntimeError, match="published to the server, but these export targets failed: pages"
    ):
        _run_linked_exports(job, broken_root, "site", broken, apply=True)


def _checkout_or_none(tmp_path: Path, bare: Path) -> bool:
    proc = subprocess.run(
        ["git", "ls-remote", "--heads", bare.as_uri()], capture_output=True, text=True, check=True
    )
    return bool(proc.stdout.strip())
