"""The desktop app: the studio in the background, a tray icon, and the browser.

Opening ConstellationCMS from the Start menu (or the applications menu on Linux) runs this. It
starts the studio on this computer only, opens it in the default browser, and shows a tray icon
with *Open studio* and *Quit*. Opening it again while it runs just opens the browser. Where the
desktop has no tray (some Linux desktops), the studio's own menu has a *Quit* button instead.

The installers start it as ``ConstellationCMS`` (``--background`` when starting with Windows,
so no browser window pops up at sign-in). From a Python install it is ``constellation app``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx

from constellation import __version__

log = logging.getLogger("constellation.desktop")
APP_FILE = "desktop-app.json"
PREFERRED_PORT = 8787


def default_workspace() -> Path:
    """Where the desktop app keeps sites: a folder people can find in Documents."""
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / "Constellation Sites"


def icon_image(size: int = 64) -> Any:
    """The app icon (the studio's favicon: a small constellation on black), drawn with Pillow."""
    from PIL import Image, ImageDraw

    scale = size / 24
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(5 * scale), fill=(0, 0, 0, 255))

    def p(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    line = (196, 202, 212, 160)
    width = max(1, round(scale))
    d.line([p(3, 17), p(8, 9), p(14, 12), p(20, 4)], fill=line, width=width)
    d.line([p(14, 12), p(17, 20)], fill=line, width=width)
    for x, y, r in ((3, 17, 1.6), (8, 9, 1.3), (20, 4, 1.4), (17, 20, 1.2)):
        d.ellipse((*p(x - r, y - r), *p(x + r, y + r)), fill=(243, 245, 249, 255))
    d.ellipse((*p(14 - 2.2, 12 - 2.2), *p(14 + 2.2, 12 + 2.2)), fill=(223, 0, 255, 255))
    return img


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(preferred: int = PREFERRED_PORT) -> int:
    if port_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def running_instance(state_dir: Path, machine_id: str) -> str:
    """The address of this computer's studio if the app is already running, else ""."""
    path = state_dir / APP_FILE
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        port = int(info["port"])
        # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and waits for it.
        r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2)
        if r.status_code == 200 and r.json().get("app") == machine_id:
            return f"http://localhost:{port}/"
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        pass
    return ""


class DesktopApp:
    """The studio server in a thread, plus the tray icon in the main thread."""

    def __init__(self, state_dir: Path | None = None, workspace: Path | None = None) -> None:
        from constellation.studio.state import State

        self.state = State(state_dir)
        s = self.state.settings
        if workspace is not None:
            s.workspace = str(workspace.expanduser().resolve())
        if not s.workspace:
            s.workspace = str(default_workspace())
        self.state.save_settings()
        self.workspace = Path(s.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.server: Any = None
        self.thread: threading.Thread | None = None
        self.icon: Any = None
        self.port = 0
        self.url = ""
        self.local = ""

    def start_server(self) -> None:
        import uvicorn

        from constellation.studio.app import create_app

        self.port = pick_port(int(self.state.settings.port or PREFERRED_PORT))
        self.url = f"http://localhost:{self.port}/"
        self.local = f"http://127.0.0.1:{self.port}/"  # for our own requests (see above)
        app = create_app(self.state, self.workspace)
        app.state.on_quit = self.quit
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", log_config=None
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="studio", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the studio didn't start; see the log file")
            time.sleep(0.05)
        (self.state.dir / APP_FILE).write_text(
            json.dumps({"port": self.port, "pid": os.getpid(), "version": __version__}),
            encoding="utf-8",
        )
        log.info("studio running at %s, sites in %s", self.url, self.workspace)

    def open_browser(self) -> None:
        webbrowser.open(self.url)

    def quit(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.icon is not None:
            with contextlib.suppress(Exception):
                self.icon.stop()
        with contextlib.suppress(OSError):
            (self.state.dir / APP_FILE).unlink()

    def run_tray(self) -> bool:
        """Show the tray icon until Quit. False when this desktop can't show one."""
        try:
            import pystray
        except Exception as e:  # no tray library, or no display backend for it
            log.info("no tray icon: %s", e)
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Open studio", lambda: self.open_browser(), default=True),
            pystray.MenuItem("Quit", lambda: self.quit()),
        )
        self.icon = pystray.Icon("ConstellationCMS", icon_image(64), "ConstellationCMS", menu)
        try:
            self.icon.run()
        except Exception as e:
            log.info("tray icon failed: %s", e)
            self.icon = None
            return False
        return True

    def wait(self) -> None:
        while self.thread is not None and self.thread.is_alive():
            self.thread.join(0.5)


def _setup_logging(cache_dir: Path) -> None:
    # A windowed app has no console: keep messages in a log file people can send when asking
    # for help, and give print() somewhere to go.
    cache_dir.mkdir(parents=True, exist_ok=True)
    logfile = cache_dir / "app.log"
    with contextlib.suppress(OSError):
        if logfile.stat().st_size > 2_000_000:
            logfile.replace(logfile.with_suffix(".log.old"))
    handlers: list[logging.Handler] = [logging.FileHandler(logfile, encoding="utf-8")]
    if sys.stdout is None or sys.stderr is None:
        stream = open(logfile, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    else:
        handlers.append(logging.StreamHandler())  # started from a terminal: show them there too
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def selftest(state_dir: Path, workspace: Path) -> int:
    """Start the studio, check it answers, stop. For the installer builds' smoke test."""
    app = DesktopApp(state_dir, workspace)
    app.start_server()
    try:
        base = app.local
        r = httpx.get(base + "healthz", timeout=10)
        page = httpx.get(base + "setup", timeout=10, headers={"host": f"localhost:{app.port}"})
        ok = (
            r.status_code == 200
            and r.json().get("version") == __version__
            and page.status_code == 200
            and "Welcome" in page.text
            and httpx.get(base + "static/studio.css", timeout=10).status_code == 200
            and httpx.get(base + "static/themes/base.webp", timeout=10).status_code == 200
        )
        from constellation.studio.selfwheel import build_wheel

        name, data = build_wheel()
        ok = ok and name.endswith(".whl") and len(data) > 10_000
        print(f"selftest {'ok' if ok else 'FAILED'}: {app.url} version {__version__}, {name}")
        return 0 if ok else 1
    finally:
        app.quit()
        app.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ConstellationCMS", description=__doc__.split("\n")[0])
    parser.add_argument("--background", action="store_true", help="don't open the browser")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--workspace", type=Path, default=None, help="the sites folder")
    parser.add_argument("--no-tray", action="store_true", help="don't show a tray icon")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"ConstellationCMS {__version__}")
    args = parser.parse_args(argv)

    from constellation.studio.state import State, default_cache_dir

    _setup_logging(default_cache_dir())
    if args.selftest:
        return selftest(
            args.state_dir or Path.cwd() / "selftest-state",
            args.workspace or Path.cwd() / "selftest-sites",
        )
    probe = State(args.state_dir)
    existing = running_instance(probe.dir, probe.settings.machine_id)
    probe.close()
    if existing:
        if not args.background:
            webbrowser.open(existing)
        return 0
    app = DesktopApp(args.state_dir, args.workspace)
    try:
        app.start_server()
    except Exception:
        log.exception("could not start the studio")
        return 1
    from constellation.studio import updates

    updates.start_background(app.state)
    if not args.background:
        app.open_browser()
    if args.no_tray or not app.run_tray():
        app.wait()  # no tray: the studio's Quit button stops it
    app.quit()
    app.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
