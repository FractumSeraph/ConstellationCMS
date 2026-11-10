"""Python plugins: shortcodes, text transforms, template filters and globals, and build hooks.

A plugin is a Python file in the workspace's ``plugins`` folder (``<workspace>/plugins/name.py``
or ``plugins/name/__init__.py``), in a site's own ``plugins`` folder, or an installed package
that registers itself under the ``constellation.plugins`` entry point group. The module defines
``plugin``, a :class:`Plugin`::

    from constellation.core.plugins import Plugin, Setting

    plugin = Plugin("youtube", "Embed YouTube videos", settings=[
        Setting("privacy", "bool", "Use youtube-nocookie.com", True),
    ])

    @plugin.shortcode("youtube")
    def youtube(args, kwargs, ctx):
        video = args[0]
        host = "www.youtube-nocookie.com" if ctx.settings["privacy"] else "www.youtube.com"
        return {
            "web": f'<iframe src="https://{host}/embed/{video}" allowfullscreen></iframe>',
            "gemini": f"=> https://youtu.be/{video} Watch on YouTube",
            "gopher": f"Video: https://youtu.be/{video}",
        }

A site turns plugins on in ``site.toml``::

    [plugins]
    enabled = ["youtube"]

    [plugins.settings.youtube]
    privacy = false

Plugins are ordinary Python running with your permissions while the studio or the command line
builds the site; the server never runs them. Only turn on plugins you trust.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

from constellation.core.markdown.document import Warning

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SHORTCODE = re.compile(r"\{\{<\s*([A-Za-z][\w-]*)((?:[^>]|>(?!\}\}))*?)\s*>\}\}")
PLATFORMS = ("web", "gemini", "gopher")
ENTRY_POINT_GROUP = "constellation.plugins"


@dataclass(slots=True)
class Setting:
    """One plugin setting, shown in the studio. Types: string, text, bool, int, color, choice."""

    key: str
    type: str = "string"
    label: str = ""
    default: Any = ""
    choices: list[str] = field(default_factory=list)
    help: str = ""


@dataclass(slots=True)
class Context:
    """What a hook gets: the site, this plugin's settings, and (for shortcodes) the item."""

    site: Any
    settings: dict[str, Any]
    item: Any = None


Shortcode = Callable[[list[str], dict[str, str], Context], "str | dict[str, str]"]
Transform = Callable[[str, Context], str]
BuildHook = Callable[[Any, Context], None]


class Plugin:
    """A plugin's registry. Create one as the module-level ``plugin`` and decorate functions."""

    def __init__(
        self, name: str, description: str = "", *, settings: list[Setting] | None = None
    ) -> None:
        if not NAME.match(name):
            raise ValueError(f"plugin name {name!r} must be lowercase letters, digits, - or _")
        self.name = name
        self.description = description
        self.settings = list(settings or [])
        self.shortcodes: dict[str, Shortcode] = {}
        self.transforms: list[Transform] = []
        self.filters: dict[str, Callable[..., Any]] = {}
        self.globals: dict[str, Any] = {}
        self.build_hooks: list[BuildHook] = []

    def shortcode(self, name: str) -> Callable[[Shortcode], Shortcode]:
        """``{{< name arg key="value" >}}`` in Markdown. Return Markdown for every platform, or a
        dict of Markdown per platform (``web``, ``gemini``, ``gopher``); platforms left out get
        nothing. Per-platform output is block-level, so put the shortcode on its own line."""

        def deco(fn: Shortcode) -> Shortcode:
            self.shortcodes[name] = fn
            return fn

        return deco

    def transform(self, fn: Transform) -> Transform:
        """Change an item's Markdown before it is parsed: ``fn(text, ctx) -> text``."""
        self.transforms.append(fn)
        return fn

    def filter(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """A Jinja filter for themes: ``{{ value | name }}``."""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.filters[name] = fn
            return fn

        return deco

    def template_global(self, name: str, value: Any) -> None:
        """A value or function every template can use by ``name``."""
        self.globals[name] = value

    def after_build(self, fn: BuildHook) -> BuildHook:
        """Called with the finished build: ``fn(build, ctx)``. It may add, change or remove files,
        for example ``build.platforms["web"].add("hello.txt", "hi")``."""
        self.build_hooks.append(fn)
        return fn

    def defaults(self) -> dict[str, Any]:
        return {s.key: s.default for s in self.settings}


@dataclass(slots=True)
class Available:
    """A plugin found on disk or installed, whether or not a site uses it."""

    name: str
    description: str
    origin: str
    settings: list[Setting]
    error: str = ""


@dataclass(slots=True)
class Active:
    plugin: Plugin
    settings: dict[str, Any]


@dataclass(slots=True)
class Loaded:
    """The plugins a site turned on, ready to use, and any problems loading them."""

    active: list[Active] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)

    def context(self, a: Active, site: Any, item: Any = None) -> Context:
        return Context(site, dict(a.settings), item)


_CACHE: dict[tuple[str, float], ModuleType] = {}


def plugin_dirs(site_root: Path) -> list[Path]:
    """``<workspace>/plugins`` for ``<workspace>/sites/<id>``, then the site's own ``plugins``."""
    site_root = Path(site_root)
    dirs = [site_root / "plugins"]
    if site_root.parent.name == "sites":
        dirs.insert(0, site_root.parent.parent / "plugins")
    return [d for d in dirs if d.is_dir()]


def _import_file(path: Path) -> ModuleType:
    key = (str(path.resolve()), path.stat().st_mtime)
    if key in _CACHE:
        return _CACHE[key]
    mod_name = "constellation_plugin_" + re.sub(
        r"\W", "_", path.parent.name if path.name == "__init__.py" else path.stem
    )
    spec = importlib.util.spec_from_file_location(
        mod_name,
        path,
        submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _CACHE[key] = module
    return module


def _load_entry_point(ep: Any) -> ModuleType:
    module: ModuleType = ep.load()
    return module


def _candidates(site_root: Path) -> dict[str, tuple[str, Callable[[], ModuleType]]]:
    """name → (where it comes from, loader). Folders win over installed packages."""
    found: dict[str, tuple[str, Callable[[], ModuleType]]] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found.setdefault(ep.name, (f"package {ep.value}", partial(_load_entry_point, ep)))
    for d in reversed(plugin_dirs(site_root)):
        for p in sorted(d.iterdir()):
            if p.name.startswith((".", "_")):
                continue
            if p.is_file() and p.suffix == ".py":
                found[p.stem] = (str(p), partial(_import_file, p))
            elif p.is_dir() and (p / "__init__.py").is_file():
                found[p.name] = (str(p), partial(_import_file, p / "__init__.py"))
    return found


def _plugin_of(module: Any, name: str) -> Plugin:
    plugin = getattr(module, "plugin", module if isinstance(module, Plugin) else None)
    if not isinstance(plugin, Plugin):
        raise TypeError(f"{name} does not define `plugin = Plugin(...)`")
    return plugin


def available(site_root: Path) -> list[Available]:
    """Every plugin the studio can offer this site, with load errors reported, not raised."""
    out: list[Available] = []
    for name, (origin, load) in sorted(_candidates(site_root).items()):
        try:
            p = _plugin_of(load(), name)
            out.append(Available(name, p.description, origin, p.settings))
        except Exception as e:  # noqa: BLE001 - shown to the user
            out.append(Available(name, "", origin, [], error=f"{type(e).__name__}: {e}"))
    return out


def load(site: Any) -> Loaded:
    """The plugins ``site`` turned on, in the order listed in ``[plugins] enabled``."""
    cfg = site.config.plugins
    loaded = Loaded()
    if not cfg.enabled:
        return loaded
    candidates = _candidates(site.root)
    for name in cfg.enabled:
        if name not in candidates:
            loaded.warnings.append(
                Warning(
                    "PLUGIN-MISSING",
                    "error",
                    "all",
                    0,
                    f"plugin {name!r} is turned on but was not found in the plugins folder or installed packages",
                )
            )
            continue
        try:
            plugin = _plugin_of(candidates[name][1](), name)
        except Exception as e:  # noqa: BLE001 - reported as a build error
            loaded.warnings.append(
                Warning(
                    "PLUGIN-ERROR",
                    "error",
                    "all",
                    0,
                    f"plugin {name!r} failed to load: {type(e).__name__}: {e}",
                )
            )
            continue
        settings = plugin.defaults()
        settings.update(cfg.settings.get(name, {}))
        loaded.active.append(Active(plugin, settings))
    return loaded


def _split_args(raw: str) -> tuple[list[str], dict[str, str]]:
    args: list[str] = []
    kwargs: dict[str, str] = {}
    for tok in shlex.split(raw, posix=True):
        key, eq, value = tok.partition("=")
        if eq and re.fullmatch(r"[A-Za-z_][\w-]*", key):
            kwargs[key] = value
        else:
            args.append(tok)
    return args, kwargs


def expand(
    text: str, loaded: Loaded, site: Any, item: Any = None, *, warnings: list[Warning] | None = None
) -> str:
    """Run the transforms, then replace shortcodes, outside fenced code blocks and inline code."""
    if not loaded.active:
        return text
    sink = warnings if warnings is not None else loaded.warnings
    label = f"{item.kind}:{item.slug}" if item is not None else "preview"
    for a in loaded.active:
        for fn in a.plugin.transforms:
            try:
                text = fn(text, loaded.context(a, site, item))
            except Exception as e:  # noqa: BLE001
                sink.append(
                    Warning(
                        "PLUGIN-ERROR",
                        "error",
                        "all",
                        0,
                        f"{label}: plugin {a.plugin.name!r} transform failed: {type(e).__name__}: {e}",
                    )
                )
    codes: dict[str, Active] = {}
    for a in loaded.active:
        for name in a.plugin.shortcodes:
            codes.setdefault(name, a)
    if not codes or "{{<" not in text:
        return text

    def run(m: re.Match[str], line_no: int) -> str:
        name, raw = m.group(1), m.group(2)
        a = codes.get(name)
        if a is None:
            sink.append(
                Warning(
                    "SHORTCODE-UNKNOWN",
                    "warn",
                    "all",
                    line_no,
                    f"{label}: no plugin provides the shortcode {name!r}",
                )
            )
            return m.group(0)
        try:
            args, kwargs = _split_args(raw)
            result = a.plugin.shortcodes[name](args, kwargs, loaded.context(a, site, item))
        except Exception as e:  # noqa: BLE001
            sink.append(
                Warning(
                    "PLUGIN-ERROR",
                    "error",
                    "all",
                    line_no,
                    f"{label}: shortcode {name!r} failed: {type(e).__name__}: {e}",
                )
            )
            return ""
        if isinstance(result, dict):
            parts = []
            for platform in PLATFORMS:
                body = str(result.get(platform, "")).strip("\n")
                if body:
                    parts.append(f"::: only-{platform}\n{body}\n:::")
            return "\n\n" + "\n\n".join(parts) + "\n\n" if parts else ""
        return str(result)

    out: list[str] = []
    fence = ""
    for n, line in enumerate(text.split("\n"), start=1):
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            out.append(line)
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            out.append(line)
            continue
        if "{{<" in line:
            # Leave `inline code` alone: expand only the parts between backtick spans.
            pieces = re.split(r"(`+[^`]*`+)", line)
            line = "".join(
                p if p.startswith("`") else SHORTCODE.sub(lambda m, n=n: run(m, n), p)
                for p in pieces
            )
        out.append(line)
    return "\n".join(out)


def install_templates(env: Any, loaded: Loaded) -> None:
    for a in loaded.active:
        env.filters.update(a.plugin.filters)
        env.globals.update(a.plugin.globals)


def after_build(build: Any, loaded: Loaded, site: Any) -> None:
    for a in loaded.active:
        for fn in a.plugin.build_hooks:
            try:
                fn(build, loaded.context(a, site))
            except Exception as e:  # noqa: BLE001
                w = Warning(
                    "PLUGIN-ERROR",
                    "error",
                    "all",
                    0,
                    f"plugin {a.plugin.name!r} after_build failed: {type(e).__name__}: {e}",
                )
                for pb in build.platforms.values():
                    pb.warnings.append(w)
