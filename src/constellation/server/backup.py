"""``constellation server backup``: a tarball of everything needed to rebuild the data directory.

Release trees are hard links into the blob store, so they are left out and rebuilt from the
manifests on restore; blobs, manifests, pointers, tokens, certificates, analytics and server.toml
are included.
"""

from __future__ import annotations

import datetime as dt
import os
import tarfile
from pathlib import Path


def backup(data_dir: Path, out_dir: Path) -> Path:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"constellation-backup-{stamp}.tar.gz"
    tmp = target.with_suffix(".tmp")
    # The archive holds private keys, token hashes and visitor addresses.
    tmp.touch(mode=0o600)
    os.chmod(tmp, 0o600)
    with tarfile.open(tmp, "w:gz") as tar:
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(data_dir)
            parts = rel.parts
            # sites/<site>/releases/<id>/<platform>/... are rebuilt from blobs; keep the manifests.
            if (
                len(parts) >= 5
                and parts[0] == "sites"
                and parts[2] == "releases"
                and parts[4] != "manifest.json"
            ):
                continue
            if path.suffix in (".tmp",) or path.name.endswith("-wal") or path.name.endswith("-shm"):
                continue
            tar.add(path, arcname=str(rel))
    tmp.replace(target)
    return target


def restore(archive: Path, data_dir: Path) -> list[str]:
    """Unpack a backup into an empty data directory and rebuild release trees. Returns the sites."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if any(data_dir.iterdir()):
        raise RuntimeError(f"{data_dir} is not empty")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError(f"unsafe path in archive: {member.name}")
        tar.extractall(data_dir, filter="data")
    from constellation.server.releases import Store

    store = Store(data_dir)
    for sid in store.sites():
        # Rebuild trees only; the active release, retired sites and retention order stay as
        # they were when the backup was taken.
        store.site(sid).rebuild_trees()
    return store.sites()
