"""Make the theme pictures the studio's first-run wizard shows.

Builds the demo site once per built-in theme, takes a screenshot of its home page with
Playwright, and saves a small WebP for each in the studio's static folder:

    uv run python scripts/theme_previews.py

Run it again after changing a theme's look. Needs Playwright's Chromium
(``uv run playwright install chromium``).
"""

from __future__ import annotations

import functools
import http.server
import io
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import tomlkit
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from constellation.core.build.pipeline import build_site, write_build  # noqa: E402
from constellation.core.content import load_site  # noqa: E402
from constellation.core.themes import BUILTIN_DIR  # noqa: E402

DEMO = ROOT / "examples" / "demo-site"
OUT = ROOT / "src" / "constellation" / "studio" / "static" / "themes"
WIDTH = 520


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    themes = sorted(p.name for p in BUILTIN_DIR.iterdir() if (p / "theme.toml").is_file())
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        for name in themes:
            site = Path(tmp) / name
            shutil.copytree(DEMO, site)
            doc = tomlkit.parse((site / "site.toml").read_text(encoding="utf-8"))
            doc["site"]["theme"] = name  # type: ignore[index]
            (site / "site.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")
            out = Path(tmp) / f"{name}-out"
            write_build(build_site(load_site(site), platforms=["web"]), out)
            handler = functools.partial(Quiet, directory=str(out / "web"))
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            httpd.shutdown()
            shot = Image.open(io.BytesIO(page.screenshot()))
            shot = shot.resize((WIDTH, round(shot.height * WIDTH / shot.width)), Image.LANCZOS)
            shot.save(OUT / f"{name}.webp", "WEBP", quality=72, method=6)
            print(f"{name}: {(OUT / f'{name}.webp').stat().st_size // 1024} KB")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
