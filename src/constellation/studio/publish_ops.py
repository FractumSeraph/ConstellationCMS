"""Export/publish operations run inside studio jobs."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from constellation.core.build.pipeline import build_site
from constellation.core.content import load_site
from constellation.publish.export import (
    apply_export,
    export_rsync,
    layout_files,
    open_target,
    plan_export,
)
from constellation.studio.jobs import Job
from constellation.studio.workspace import atomic_write


def run_export(
    job: Job, root: Path, site_id: str, spec: dict[str, Any], *, apply: bool
) -> dict[str, Any]:
    platform = str(spec.get("platform", "web"))
    target_spec = str(spec.get("target", ""))
    layout = str(spec.get("layout", "plain"))
    job.say(f"loading {site_id}")
    site = load_site(root, site_id=site_id)
    now = datetime.now(UTC)
    job.say(f"building {platform}")
    build = build_site(site, platforms=[platform], now=now)
    errors = [w for w in build.platforms[platform].warnings if w.severity == "error"]
    if errors:
        for w in errors:
            job.say(f"error {w.code}: {w.message}")
        raise RuntimeError(f"{len(errors)} error-level warnings; fix them before exporting")
    files = layout_files(build, platform, layout, site)
    if target_spec.startswith("rsync:"):
        out = export_rsync(files, target_spec, dry_run=not apply)
        for line in out.splitlines():
            job.say(line)
        result = {"mode": "rsync", "applied": apply}
    else:
        target = open_target(target_spec)
        try:
            plan = plan_export(files, target)
            job.say(f"{target.describe()}: {plan.summary()}")
            for p in plan.added:
                job.say(f"+ {p}")
            for p in plan.changed:
                job.say(f"~ {p}")
            for p in plan.removed:
                job.say(f"- {p}")
            if apply and not plan.empty:
                apply_export(files, target, plan)
                job.say("applied")
            elif apply:
                job.say("nothing to do")
            result = {
                "added": len(plan.added),
                "changed": len(plan.changed),
                "removed": len(plan.removed),
                "applied": apply,
            }
        finally:
            target.close()
    if apply:
        _record_release(
            root,
            {
                "time": now.isoformat(),
                "kind": "export",
                "name": spec.get("name", ""),
                "platform": platform,
                "release": build.release_id(),
                "target": target_spec,
            },
        )
    return result


def _record_release(root: Path, entry: dict[str, Any]) -> None:
    p = root / ".constellation" / "releases.json"
    data: dict[str, Any] = {"version": 1, "releases": []}
    if p.is_file():
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("releases", []).append(entry)
    data["releases"] = data["releases"][-200:]
    atomic_write(p, json.dumps(data, indent=1))
