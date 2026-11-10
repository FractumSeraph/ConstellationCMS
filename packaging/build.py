"""Build the desktop app: the Windows installer, or the Linux AppImage and .deb.

    uv sync --extra build
    uv run python packaging/build.py            # build for this system, then test the result
    uv run python packaging/build.py --no-installer   # just the program folder (quicker)

Everything lands in ``build/desktop/``; the finished downloads in ``build/desktop/out/``. GitHub
Actions runs this for every release (``.github/workflows/release.yml``) and in CI.

Steps:

1. Build ConstellationCMS's wheel. It goes inside the app, so the studio can install exactly
   this version on a server (the guided server setup uploads it).
2. Draw the icons.
3. Freeze the app with PyInstaller (Python and every library included; nothing else to install).
4. Run the frozen app's self-test: start the studio, fetch a few pages, stop.
5. Windows: make ``ConstellationCMS-<version>-Setup.exe`` with Inno Setup.
   Linux: make ``ConstellationCMS-<version>-x86_64.AppImage`` and ``constellationcms_<version>_amd64.deb``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build" / "desktop"
OUT = BUILD / "out"
NAME = "ConstellationCMS"
APPIMAGETOOL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
WINDOWS = sys.platform == "win32"


def version() -> str:
    text = (ROOT / "src" / "constellation" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__ = "([^"]+)"', text)
    if not m:
        raise SystemExit("no __version__ in src/constellation/__init__.py")
    return m.group(1)


def run(*args: str | Path, **kw: object) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)  # type: ignore[call-overload]


def build_wheel() -> Path:
    wheel_dir = BUILD / "wheel"
    shutil.rmtree(wheel_dir, ignore_errors=True)
    run("uv", "build", "--wheel", "--out-dir", wheel_dir, cwd=ROOT)
    wheels = sorted(wheel_dir.glob("constellationcms-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel in {wheel_dir}, found {wheels}")
    return wheels[0]


def make_icons() -> tuple[Path, Path]:
    sys.path.insert(0, str(ROOT / "src"))
    from constellation.desktop import icon_image

    ico, png = BUILD / f"{NAME}.ico", BUILD / "constellationcms.png"
    big = icon_image(256)
    big.save(png)
    big.save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return ico, png


def freeze(wheel: Path, ico: Path) -> Path:
    sep = os.pathsep
    args: list[str | Path] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        NAME,
        "--distpath",
        BUILD / "dist",
        "--workpath",
        BUILD / "work",
        "--specpath",
        BUILD,
        "--collect-data",
        "constellation",
        "--collect-submodules",
        "constellation",
        "--copy-metadata",
        "constellationcms",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "mdit_py_plugins",
        "--collect-data",
        "tzdata",
        "--collect-data",
        "certifi",
        "--add-data",
        f"{wheel}{sep}constellation/_bundled",
    ]
    if WINDOWS:
        args += ["--windowed", "--icon", ico, "--hidden-import", "pystray._win32"]
    else:
        args += ["--hidden-import", "pystray._xorg"]
    args.append(PACKAGING / "launcher.py")
    run(*args, cwd=ROOT)
    return BUILD / "dist" / NAME


def selftest(app_dir: Path) -> None:
    exe = app_dir / (f"{NAME}.exe" if WINDOWS else NAME)
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, CONSTELLATION_CACHE_DIR=str(Path(tmp) / "cache"))
        out = subprocess.run(
            [str(exe), "--selftest"],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        log = Path(tmp) / "cache" / "app.log"
        print(out.stdout, out.stderr, log.read_text(encoding="utf-8") if log.exists() else "")
        if out.returncode != 0:
            raise SystemExit(f"the frozen app's self-test failed (exit {out.returncode})")
    print("self-test passed")


def find_iscc() -> str:
    candidates = [
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise SystemExit("Inno Setup 6 is not installed (https://jrsoftware.org/isdl.php)")


def windows_installer(app_dir: Path, ico: Path, ver: str) -> Path:
    run(
        find_iscc(),
        f"/DAppVersion={ver}",
        f"/DIconFile={ico}",
        f"/DSourceDir={app_dir}",
        f"/DOutputDir={OUT}",
        PACKAGING / "windows" / f"{NAME}.iss",
    )
    return OUT / f"{NAME}-{ver}-Setup.exe"


def _copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)


def appimage(app_dir: Path, png: Path, ver: str) -> Path:
    appdir = BUILD / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    _copytree(app_dir, appdir / "usr" / "lib" / "constellationcms")
    desktop = (PACKAGING / "linux" / "constellationcms.desktop").read_text(encoding="utf-8")
    (appdir / "constellationcms.desktop").write_text(desktop, encoding="utf-8")
    shutil.copy(png, appdir / "constellationcms.png")
    shutil.copy(png, appdir / ".DirIcon")
    apprun = appdir / "AppRun"
    apprun.write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
        'exec "$HERE/usr/lib/constellationcms/ConstellationCMS" "$@"\n',
        encoding="utf-8",
    )
    apprun.chmod(0o755)
    tool = BUILD / "appimagetool"
    if not tool.exists():
        print(f"downloading {APPIMAGETOOL}")
        urllib.request.urlretrieve(APPIMAGETOOL, tool)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    target = OUT / f"{NAME}-{ver}-x86_64.AppImage"
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    run(tool, "--no-appstream", appdir, target, env=env)
    return target


def deb(app_dir: Path, png: Path, ver: str) -> Path:
    root = BUILD / "deb"
    shutil.rmtree(root, ignore_errors=True)
    _copytree(app_dir, root / "opt" / "constellationcms")
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "constellationcms").symlink_to("/opt/constellationcms/ConstellationCMS")
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    shutil.copy(PACKAGING / "linux" / "constellationcms.desktop", apps)
    icons = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)
    shutil.copy(png, icons / "constellationcms.png")
    size_kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) // 1024
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN" / "control").write_text(
        f"Package: constellationcms\nVersion: {ver}\nSection: web\nPriority: optional\n"
        f"Architecture: amd64\nInstalled-Size: {size_kb}\n"
        "Maintainer: ConstellationCMS <https://github.com/FractumSeraph/ConstellationCMS>\n"
        "Homepage: https://github.com/FractumSeraph/ConstellationCMS\n"
        "Description: Write a blog or website and publish it to the web and Gemini\n"
        " ConstellationCMS is a studio for writing a site once and publishing it to the web,\n"
        " Gemini and Gopher, on your own computer, your own server or GitHub Pages.\n",
        encoding="utf-8",
    )
    target = OUT / f"constellationcms_{ver}_amd64.deb"
    run("dpkg-deb", "--root-owner-group", "--build", root, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-installer", action="store_true", help="stop after the self-test")
    parser.add_argument("--no-selftest", action="store_true")
    args = parser.parse_args()
    ver = version()
    BUILD.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    wheel = build_wheel()
    ico, png = make_icons()
    app_dir = freeze(wheel, ico)
    if not args.no_selftest:
        selftest(app_dir)
    if args.no_installer:
        return 0
    made = [shutil.copy(wheel, OUT / wheel.name)]
    if WINDOWS:
        made.append(str(windows_installer(app_dir, ico, ver)))
    else:
        made += [str(appimage(app_dir, png, ver)), str(deb(app_dir, png, ver))]
    print("built:")
    for m in made:
        print(f"  {m}  ({Path(m).stat().st_size // (1024 * 1024)} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
