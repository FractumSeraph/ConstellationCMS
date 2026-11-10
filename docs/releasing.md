# Making a release

A release is a `v…` tag. GitHub Actions builds everything and publishes it on the releases page:

- the Python wheel (`constellationcms-<version>-py3-none-any.whl`),
- the Windows installer (`ConstellationCMS-<version>-Setup.exe`),
- the Linux AppImage (`ConstellationCMS-<version>-x86_64.AppImage`) and `.deb`
  (`constellationcms_<version>_amd64.deb`).

The studio's update check reads the newest release, and offers the download that matches how it
was installed. Downloads need the repository to be public.

## Steps

1. Set the new version in `src/constellation/__init__.py` (`__version__`) and in
   `pyproject.toml` (`version`), and move the CHANGELOG's *Unreleased* notes under a heading
   for it.
2. Commit and push to `main`, and wait for CI to pass (it also builds and tests the desktop app
   on Windows and Linux).
3. Tag and push the tag:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

4. The *Release* workflow checks that the tag matches `__version__`, builds on Windows and
   Ubuntu 22.04, and creates the GitHub release with the files attached and notes generated
   from the commits. Edit the notes on GitHub afterwards if you like.

## Building by hand

```bash
uv sync --extra build
uv run python packaging/build.py
```

This builds for the system it runs on into `build/desktop/out/`, after running the frozen app's
self-test. The Windows installer needs [Inno Setup 6](https://jrsoftware.org/isdl.php); the
Linux build needs `dpkg-deb` and downloads `appimagetool`. `--no-installer` stops after the
program folder and its self-test, which is quicker when trying changes.

The pictures of the themes in the first-run wizard are made with
`uv run python scripts/theme_previews.py` (run it after changing how a built-in theme looks).
