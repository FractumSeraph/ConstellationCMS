"""Theme loading: templates per platform, settings schema, sandboxed Jinja environments."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, FileSystemLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

BUILTIN_DIR = Path(__file__).resolve().parent.parent.parent / "themes_builtin"


class ThemeError(Exception):
    pass


@dataclass(slots=True)
class ThemeSetting:
    key: str
    type: str
    label: str
    default: Any = None


@dataclass(slots=True)
class Theme:
    name: str
    path: Path
    platforms: list[str]
    extends: str | None
    settings: list[ThemeSetting] = field(default_factory=list)
    version: str = ""
    description: str = ""

    def templates_dir(self, platform: str) -> Path:
        return self.path / "templates" / platform

    def static_dir(self) -> Path:
        return self.path / "static"

    def defaults(self) -> dict[str, Any]:
        return {s.key: s.default for s in self.settings}


def load_theme(name: str, *, search: list[Path] | None = None) -> Theme:
    """Find a theme by name in the search paths, then among the built-in themes."""
    candidates = [*(search or []), BUILTIN_DIR]
    for base in candidates:
        path = base / name
        if (path / "theme.toml").is_file():
            return _read(path)
    raise ThemeError(
        f"theme {name!r} not found (looked in {', '.join(str(c) for c in candidates)})"
    )


def _read(path: Path) -> Theme:
    with (path / "theme.toml").open("rb") as fh:
        data = tomllib.load(fh)
    t = data.get("theme", {})
    settings = [
        ThemeSetting(s["key"], s.get("type", "string"), s.get("label", s["key"]), s.get("default"))
        for s in data.get("settings", [])
    ]
    return Theme(
        name=t.get("name", path.name),
        path=path,
        platforms=list(t.get("platforms", ["web"])),
        extends=t.get("extends") or (None if path.name == "base" else "base"),
        settings=settings,
        version=t.get("version", ""),
        description=t.get("description", ""),
    )


def theme_chain(theme: Theme, *, search: list[Path] | None = None) -> list[Theme]:
    """The theme followed by the themes it extends, nearest first."""
    chain = [theme]
    seen = {theme.name}
    current = theme
    while current.extends and current.extends not in seen:
        current = load_theme(current.extends, search=search)
        chain.append(current)
        seen.add(current.name)
    return chain


def make_environment(
    chain: list[Theme], platform: str, *, overrides: Path | None = None
) -> SandboxedEnvironment:
    dirs: list[str] = []
    if overrides is not None and (overrides / platform).is_dir():
        dirs.append(str(overrides / platform))
    for t in chain:
        d = t.templates_dir(platform)
        if d.is_dir():
            dirs.append(str(d))
    env = SandboxedEnvironment(
        loader=ChoiceLoader([FileSystemLoader(d) for d in dirs]),
        autoescape=select_autoescape(["html", "xml"]) if platform == "web" else False,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env
