# Install the studio on Windows

These steps put the studio on a Windows 10 or 11 computer, such as a laptop, so you can write and
publish from it. It takes about ten minutes. If you only want to write in a browser, a
[studio on your server](studio-on-a-server.md) needs nothing installed at all.

You'll use **PowerShell** (Start menu → type *PowerShell*). Each grey box is one command: copy
it, paste it into PowerShell and press Enter.

## 1. Install the tools

```powershell
winget install astral-sh.uv Git.Git GitHub.cli
```

This installs **uv** (which installs and updates ConstellationCMS), **Git** and the
**GitHub CLI** (used when publishing to GitHub Pages). Close PowerShell and open it again
afterwards, so it finds the new commands.

## 2. Sign in to GitHub

Needed to publish to GitHub Pages. Skip it if you don't use GitHub.

```powershell
gh auth login
```

Choose *GitHub.com*, *HTTPS*, and *Login with a web browser*, and follow the browser steps. Then
let Git use that sign-in:

```powershell
gh auth setup-git
```

## 3. Install ConstellationCMS

```powershell
uv tool install git+https://github.com/FractumSeraph/ConstellationCMS
```

Check it worked:

```powershell
constellation --version
```

To update later, run the install command again with `--force` after `install`.

## 4. Get your sites onto this computer

Your sites live in a folder (the *workspace*), for example `ConstellationSites`, with a `sites`
folder and `workspace.toml` inside.

- **Starting fresh?** Skip this step: the studio creates the folder.
- **Already writing on another computer or a server studio?** Keep the folders the same on every
  machine with [Syncthing](https://syncthing.net/), as below. Another sync tool (OneDrive,
  Dropbox, Nextcloud) also works; put the folder inside it.

### Syncthing

```powershell
winget install Syncthing.Syncthing
```

Start it, and make it start every time you sign in to Windows:

```powershell
$exe = (Get-Item (Get-Command syncthing).Source).Target
Register-ScheduledTask -TaskName Syncthing -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Action (New-ScheduledTaskAction -Execute $exe -Argument 'serve --no-browser --no-console') -Force
Start-ScheduledTask -TaskName Syncthing
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
4. Within a minute this computer asks to add the shared folder. Accept it and choose where it
   goes, for example `C:\Users\you\ConstellationSites`. Wait until it says *Up to Date*.

## 5. Start the studio

```powershell
constellation studio --workspace "$env:USERPROFILE\ConstellationSites"
```

Your browser opens http://localhost:8787. The first time, choose a password for this computer's
studio and a look. Next time, plain `constellation studio` is enough: it remembers the folder.
Leave the PowerShell window open while you work; close it (or press Ctrl+C) to stop the studio.

## 6. Let this computer publish

Connect this computer to your server once. On a computer that is already connected, open
**Servers** and choose **Add another computer**: it shows a short pairing code, good for ten
minutes. On this computer, open **Servers**, and either enter the code in the server's
**Connect this computer** box (when the server is already listed, because your sites folder
synced it), or use **Add a server** with the server's address and the code. That's all: sites
that publish to that server get their publish keys by themselves.

Each computer gets its own connection, listed under **Connected computers** on the Servers page,
where any of them can be removed (a lost laptop, say) without affecting the others.

If a site also publishes to GitHub Pages, step 2 already took care of that.

## Good to know

- Write a given post on one computer at a time. Syncthing copies saves within seconds, and the
  studio warns you if another computer has the same post open.
- Syncthing is not a backup: deletions sync too. Turn on *File Versioning* for the folder in
  Syncthing if you want deleted files kept for a while.
- Something wrong? The studio's **Doctor** page runs a set of checks.
