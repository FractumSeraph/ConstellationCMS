# Upgrading and backups

## The studio (your PC)

```
uv tool upgrade constellationcms      # or: pipx upgrade constellationcms
```

Content, settings and themes live in your workspace and are never touched by an upgrade. The
machine-local state (`studio.toml`, sessions, the file index) is in your user profile:
`%APPDATA%\ConstellationCMS` on Windows, `~/.config/constellation` on Linux. Back it up if you
want to keep publish tokens and the second-factor secret; otherwise reconnect servers with an
admin token after a reinstall.

## The server (VPS)

```
uv pip install --python /opt/constellation --upgrade constellationcms
systemctl restart constellation
```

Releases, blobs, certificates, tokens and analytics live under the data directory
(`/var/lib/constellation` by default) and survive upgrades. Before a major upgrade:

```
constellation server backup --config /var/lib/constellation/server.toml --out /root/backups
```

The tarball holds everything except the release trees, which are hard links into the blob store
and are rebuilt on restore:

```
constellation server restore /root/backups/constellation-backup-<stamp>.tar.gz --data-dir /var/lib/constellation
```

Restore into an empty directory, review `server.toml`, then start the service. The admin token
is not in the backup in clear text (only its hash); run `server install` again if you have lost
it, or keep the token in your password manager.

## Breaking changes

None yet. This file lists them per version from 0.2.0 on.
