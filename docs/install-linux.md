# Install the studio on Linux

These steps put the studio on a Linux desktop or laptop (Ubuntu, Debian, Fedora, Arch and others)
so you can write and publish from it. It takes about ten minutes. If you only want to write in a
browser, a [studio on your server](studio-on-a-server.md) needs nothing installed at all.

Each grey box is one command for a terminal.

## 1. Install the tools

**Git, the GitHub CLI and Syncthing**, from your distribution:

```bash
# Ubuntu, Debian, Mint, Pop!_OS
sudo apt install git gh syncthing

# Fedora
sudo dnf install git gh syncthing

# Arch, Manjaro
sudo pacman -S git github-cli syncthing
```

(On older Ubuntu or Debian releases without a `gh` package, follow
https://github.com/cli/cli/blob/trunk/docs/install_linux.md.)

**uv**, which installs and updates ConstellationCMS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the terminal and open a new one, so it finds `uv`.

## 2. Sign in to GitHub

Needed to publish to GitHub Pages. Skip it if you don't use GitHub.

```bash
gh auth login
```

Choose *GitHub.com*, *HTTPS*, and *Login with a web browser*. Then let Git use that sign-in:

```bash
gh auth setup-git
```

## 3. Install ConstellationCMS

```bash
uv tool install git+https://github.com/FractumSeraph/ConstellationCMS
```

uv picks a suitable Python (3.12 or newer) by itself. Check it worked:

```bash
constellation --version
```

To update later, run the install command again with `--force` after `install`.

## 4. Get your sites onto this computer

Your sites live in a folder (the *workspace*), for example `~/ConstellationSites`, with a
`sites` folder and `workspace.toml` inside.

- **Starting fresh?** Skip this step: the studio creates the folder.
- **Already writing on another computer or a server studio?** Keep the folders the same on every
  machine with Syncthing, as below.

### Syncthing

Start it now and every time you sign in:

```bash
systemctl --user enable --now syncthing
```

Open Syncthing's page at http://127.0.0.1:8384 and connect it to the machine that already has
your sites (your server, if it runs Syncthing, is the best choice because it is always on):

1. On this computer: **Actions → Show ID**, and copy the ID.
2. On the other machine's Syncthing page: **Add Remote Device**, paste this computer's ID, and on
   the *Sharing* tab tick your sites folder. (On a server without a desktop, add it from the
   command line:
   `syncthing cli config devices add --device-id THIS-COMPUTERS-ID`, then
   `syncthing cli config folders FOLDER-ID devices add --device-id THIS-COMPUTERS-ID`, run as the
   user Syncthing runs as.)
3. Back on this computer: **Add Remote Device** with the other machine's ID. For a server, put
   `tcp://your-server.example.org:22000, dynamic` under *Advanced → Addresses*.
4. Within a minute this computer asks to add the shared folder. Accept it and set the folder path
   to `~/ConstellationSites`. Wait until it says *Up to Date*.

## 5. Start the studio

```bash
constellation studio --workspace ~/ConstellationSites
```

Your browser opens http://localhost:8787. The first time, choose a password for this computer's
studio and a look. Next time, plain `constellation studio` is enough: it remembers the folder.
Press Ctrl+C in the terminal to stop it.

### Optional: start the studio automatically

To have the studio running whenever you're signed in, without a terminal window:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/constellation-studio.service <<'EOF'
[Unit]
Description=ConstellationCMS studio

[Service]
ExecStart=%h/.local/bin/constellation studio --no-browser
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now constellation-studio
```

Then open http://localhost:8787 whenever you want to write.

## 6. Let this computer publish

Publish keys stay on each computer, so connect this one to your server once: in the studio, open
**Servers**, choose **connect** next to each site, and paste the server's admin token (it was
printed when the server was installed; keep it in a password manager). The studio keeps a
publish key for that site and forgets the admin token.

If a site also publishes to GitHub Pages, step 2 already took care of that.

## Good to know

- Write a given post on one computer at a time. Syncthing copies saves within seconds, and the
  studio warns you if another computer has the same post open.
- Syncthing is not a backup: deletions sync too. Turn on *File Versioning* for the folder in
  Syncthing if you want deleted files kept for a while.
- Something wrong? The studio's **Doctor** page runs a set of checks.
