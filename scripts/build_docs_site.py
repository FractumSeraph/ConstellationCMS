"""Build the documentation site with ConstellationCMS itself.

Usage: uv run python scripts/build_docs_site.py OUT_DIR

Assembles a site from docs/*.md and docs/plan/*.md as pages, then runs the normal build.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from constellation.core.build.pipeline import build_site, write_build
from constellation.core.content import load_site

ROOT = Path(__file__).resolve().parent.parent


def main(out: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="constellation-docs-"))
    site = tmp / "site"
    (site / "content" / "pages").mkdir(parents=True)
    (site / "content" / "posts").mkdir(parents=True)
    (site / "site.toml").write_text(
        """[site]
title = "ConstellationCMS documentation"
tagline = "Built with ConstellationCMS"
theme = "nebula"

[platforms.web]
enabled = true
base_url = "https://example.org"
hostnames = ["example.org"]

[platforms.gemini]
enabled = true
base_url = "gemini://example.org"
hostnames = ["example.org"]

[menu]
items = [{ label = "Home", ref = "home" }, { label = "Plan", ref = "page:plan" }, { label = "Themes", ref = "page:themes" }]
""",
        encoding="utf-8",
    )
    repo = "https://github.com/FractumSeraph/ConstellationCMS/blob/main/docs/"
    sources = sorted((ROOT / "docs" / "plan").glob("*.md")) + [
        ROOT / "docs" / "themes.md",
        ROOT / "docs" / "UPGRADING.md",
    ]
    for order, src in enumerate(sources, start=1):
        text = src.read_text(encoding="utf-8")
        title_match = re.search(r"^# (.+)$", text, re.M)
        title = title_match.group(1) if title_match else src.stem
        body = re.sub(r"^# .+\n", "", text, count=1, flags=re.M)
        body = re.sub(r"\]\(([0-9]{2}-[a-z-]+)\.md\)", lambda m: f"](page:{m.group(1)})", body)
        body = re.sub(
            r"\]\(\.\./research/([A-Za-z0-9_.-]*)\)",
            lambda m: f"]({repo}research/{m.group(1)})",
            body,
        )
        body = body.replace("](../research/)", f"]({repo}research/)")
        slug = "plan" if src.name == "README.md" and src.parent.name == "plan" else src.stem.lower()
        (site / "content" / "pages" / f"{slug}.md").write_text(
            f"---\ntitle: {title!r}\nmenu_order: {order}\n---\n\n{body}", encoding="utf-8"
        )
    build = build_site(load_site(site, site_id="docs"))
    write_build(build, out)
    errors = [w for pb in build.platforms.values() for w in pb.warnings if w.severity == "error"]
    for w in errors:
        print(f"error {w.code}: {w.message}")
    print(
        f"docs site: {sum(len(pb.files) for pb in build.platforms.values())} files, {len(build.warnings)} warnings, {len(errors)} errors"
    )
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "docs"))
