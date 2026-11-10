# The desktop app

The easiest way to use ConstellationCMS: download one file, install it like any other program,
and start writing. Python and everything else it needs come inside; there is nothing else to
install and no terminal.

## Download

Get the newest version from the
**[releases page](https://github.com/FractumSeraph/ConstellationCMS/releases/latest)**:

| Your computer | Download | Then |
|---|---|---|
| **Windows 10 or 11** | `ConstellationCMS-…-Setup.exe` | Open it and follow the steps. |
| **Ubuntu, Debian, Mint, Pop!_OS** | `constellationcms_…_amd64.deb` | Open it with Software Install, or run `sudo apt install ./constellationcms_*.deb`. |
| **Any other Linux** | `ConstellationCMS-…-x86_64.AppImage` | Right-click → Properties → allow running as a program, then open it. |

**Windows may warn you** the first time ("Windows protected your PC"), because the installer
isn't signed with a paid certificate. Press **More info**, then **Run anyway**.

The Windows installer installs for you only, so it doesn't ask for an administrator password. It
can put an icon on the desktop and, if you tick it, start ConstellationCMS when you sign in
(it then waits quietly in the tray until you open it).

## Using it

Open **ConstellationCMS** from the Start menu (or your applications). It opens the studio in
your web browser and puts a small constellation icon in the tray (next to the clock):

- **Click the icon** (or choose *Open studio*) to open the studio again.
- **Quit** stops it. The studio's own menu also has **Quit**, which helps on Linux desktops
  that don't show tray icons (such as GNOME without an extension).

Opening ConstellationCMS again while it runs just opens the studio in the browser.

The first time, it asks for a password (it protects the studio on this computer) and then walks
you through making your first site: its name, a look picked from pictures, and its web address
if you have one. Then you choose where it goes (just this computer for now, or a server the
studio sets up for you; see [Set up a server](set-up-a-server.md)), and the editor opens on your
first post.

## Where things are kept

- **Your sites:** `Documents\Constellation Sites` (Windows) or `~/Documents/Constellation Sites`
  (Linux). Put this folder in Syncthing, Dropbox or OneDrive to use your sites on several
  computers.
- **The studio's settings and keys** stay in your user profile (`%APPDATA%\ConstellationCMS` or
  `~/.config/constellation`) and never go in the synced folder.
- **A log file**, useful when asking for help: `%LOCALAPPDATA%\ConstellationCMS\cache\app.log` or
  `~/.cache/constellation/app.log`.

Uninstalling the app (Windows: Settings → Apps; Linux: `sudo apt remove constellationcms`, or
delete the AppImage) leaves your sites and settings where they are.

## Updates

Once a day the studio asks GitHub whether there is a newer version. If there is, a note at the
top of the studio offers the download for your computer; install it over the old one and your
sites and settings stay. Turn the check off, or check straight away, in **Account → Updates**.

## Already using `uv tool install`?

That still works and is the way to go on a server or for people who like the terminal (see
[Install on Windows](install-windows.md) and [Install on Linux](install-linux.md)). The command
`constellation app` starts the same desktop app (tray icon included, after
`uv tool install "constellationcms[desktop] @ git+https://github.com/FractumSeraph/ConstellationCMS"`).
