# 11 · Easy for everyone

Goal: someone who has never used a terminal can install ConstellationCMS, make a site, put it on
their own domain and publish it, from their own computer or their own VPS, without typing a
command, copying a token or editing a config file.

Decisions (owner, 2026-11-10):

- **Two paths:** a desktop app on the person's own computer, and a guided setup that turns the
  person's own VPS into a ConstellationCMS server. No hosting of other people's sites on the
  owner's server.
- **Addresses:** people bring their own domain. The studio tells them exactly which DNS records to
  add and checks them until they are right.
- **Sign-in:** a password is enough. The authenticator becomes optional (recommended, not
  required), including for a studio on a server, with the existing lockout after wrong
  attempts and a minimum password length of 12 for a studio reachable from the internet.
- **Desktop platforms:** Windows and Linux.

## Stages

Each stage leaves something usable and ships with tests, docs and a phone check.

### A · No more tokens

- The studio keeps each server's admin credential itself (machine-local, never in the synced
  workspace), so connecting a site to a server, minting its publish key and assigning it are
  automatic. The Servers page shows *connected* or a single **Connect** button.
- **Pairing a second computer:** on a connected studio, *Add another computer* shows a short code
  (and QR code) valid for ten minutes. The new computer enters the server's address and the code;
  the server swaps the code for that computer's own credential. Each computer's credential is
  named and can be removed from the Servers page. The server keeps a list of these instead of one
  admin token.
- A studio on a server connects to its own server automatically.

### B · Guided server setup

A wizard in the studio, **Add a server**:

1. *Where is it?* The VPS's IP address, and its root password (or a key). Links to two or three
   friendly VPS providers with the right size picked.
2. The studio connects over SSH, checks the system (Ubuntu or Debian, enough memory and disk,
   ports 80, 443 and 1965 free, or an existing Caddy it can work with), and shows a plain list of
   what it will do.
3. It installs Python's venv support, ConstellationCMS (uploading the copy the studio itself runs,
   so it works before the package is on PyPI), the service, HTTPS (built-in Let's Encrypt, or a
   Caddy block when Caddy is already there), the firewall rules, and optionally a studio on the
   server. Progress is shown step by step; every step can be retried, and a failure says what to
   do in plain words.
4. It stores the server's credential (stage A), installs a dedicated SSH key so the root password
   is never stored, and adds the server to the workspace.
5. *Your domain:* the studio lists the DNS records to create (with screenshots-style help for
   common registrars and Cloudflare), then checks DNS every few seconds and shows a green tick per
   address, then confirms HTTPS works.

The same domain check lives on each site's Settings page.

### C · Desktop app

- **Windows:** an installer (`ConstellationCMS-Setup.exe`, built with PyInstaller and Inno Setup)
  that adds a Start-menu entry and, optionally, starts with Windows. Opening it starts the studio
  in the background, shows a tray icon (*Open studio*, *Quit*), and opens the browser.
- **Linux:** an AppImage and a `.deb`, with a desktop entry and the same tray icon.
- Built by GitHub Actions for every release and attached to the GitHub release. The studio checks
  for a newer release once a day and offers to download it.
- The app bundles everything (Python included); nothing else to install.

### D · First run that makes a site

The first-run screen becomes a short wizard: password, then *Make your first site* (name, a theme
picked from pictures, web address), then *Where should it go?* (**This computer only for now**,
**My server** (stage B), or **GitHub Pages** (stage E)), then straight into writing the first post
in Visual mode.

### E · GitHub Pages with a button

*Sign in with GitHub* using GitHub's device flow (a code to type on github.com, no tokens to
copy). The studio creates the repository, turns on Pages, sets the custom domain, and publishes.
No Git or GitHub CLI needed. This needs a GitHub OAuth app registered by the owner (a client ID is
public and goes in the code).

### F · Plain words everywhere

Rename jargon in the studio (*export targets* become *Publish destinations*, *workspace* becomes
*your sites folder*, *admin token* disappears), add short help text to every setting, and turn
error messages into "what happened, what to do".

## What the owner needs to provide

- **PyPI:** publishing the package lets servers install it with one command and lets the desktop
  app update itself cleanly. Stage B works without it by uploading the studio's own copy.
- **A GitHub OAuth app** for stage E (two minutes in GitHub's settings).
- **Code signing for Windows** (optional): an unsigned installer shows a SmartScreen warning the
  first time ("More info → Run anyway"). A certificate removes it but costs money.
- **The repository public** (or releases mirrored somewhere public) so friends can download the
  desktop app.

## Order

A and B first (they remove the hardest parts and stage B depends on A), then C and D together,
then E, then F throughout.
