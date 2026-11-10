# The pieces, and a studio on your server

## What runs where

ConstellationCMS is one program, `constellation`, with two jobs. You use one or both.

| Name | Command | Runs on | What it does |
|---|---|---|---|
| **Studio** | `constellation studio` | your computer, and optionally your server too | The place you write: the editor, previews, settings, themes, analytics, and the **Publish** button. It is a web page you open in your browser. |
| **Server** | `constellation server run` (as the `constellation` service) | your VPS | Hosts the sites: serves web pages (directly or behind Caddy/nginx), Gemini and Gopher; receives what studios publish; keeps every release for rollback; counts visits. It has no pages for people to use, only a publishing API. |

Each server has an **admin address** (for example `cms.example.org`, chosen when it was installed).
That is where studios send what you publish, at `https://cms.example.org/_constellation/`.
Nobody visits it in a browser, and everything else at that address is a 404. Your sites have
their own addresses, and a studio on the server has its own (for example `studio.example.org`).

Other names you may see:

- **Your sites folder** (called the *workspace* in files and commands): the folder holding your
  sites (`sites/…`) and `workspace.toml`. Studios edit it.
- **Other places to publish** (*export targets* in `site.toml`): somewhere else a studio can
  publish a site, such as GitHub Pages, a folder or SFTP.
- **Caddy** or **nginx**: an existing web server on the VPS that passes web requests to the
  ConstellationCMS server (`behind-proxy` mode). Not part of ConstellationCMS.
- **Syncthing**: an optional, separate program that keeps a workspace in step between machines.

## A studio on the server

A studio on the server lets you write and publish from any browser, even a phone. Install it
together with the server by adding `--with-studio` and the address to use:

```bash
sudo /opt/constellation/bin/constellation server install --admin-hostname cms.example.org \
    --tls-mode behind-proxy --with-studio studio.example.org
sudo systemctl daemon-reload
sudo systemctl enable --now constellation constellation-studio
```

Point `studio.example.org` at the server in DNS like the admin address. Behind Caddy or nginx,
`constellation server proxy-snippet` includes the studio's address in the block it prints.

The install command prints a **setup link**, `https://studio.example.org/setup?code=ABCD-EFGH-JKMN`.
Open it and choose a password, scan the QR code with an authenticator app, and type the code it
shows. That's the whole setup: the studio is then open to you from anywhere, with your password
and a code from the app. You don't need to save the link: after you connect a site to the server
from your desktop studio (Servers → Add a server, with the installation key the install
command printed), its Servers page shows it. A studio on the server is connected to its own
server automatically.

The setup code is what keeps strangers out before you have chosen a password: without it the
setup page refuses, a few wrong guesses lock it for a while, and the code stops working once the
studio is set up. A studio on your own computer needs none of this; it is set up at
http://localhost:8787 with just a password.

Until a studio's owner turns on *Reaching the studio from other devices* (remote mode, which needs
the authenticator), it answers only its
own computer: the setup page is the one exception, for a server's studio waiting to be set up.

## Keeping the server's studio and your computer in step

The studio on the server keeps its own copy of your sites (`/var/lib/constellation/workspace`
by default). If you also write on your computer or a laptop, keep the copies in step with
Syncthing, sharing the workspace folder between the machines. The server is always on, so it
passes changes along even when your computer is off. The studio warns you when another machine
has the same post open.

## Publishing to GitHub Pages from the server's studio

Pushing to GitHub needs a sign-in on the server. The least-privileged way is a **deploy key**
for the one repository: create an SSH key for the user the studio runs as, add its public half
under the repository's *Settings → Deploy keys* with write access, and tell git to use SSH for
GitHub (`git config --global url."git@github.com:".insteadOf "https://github.com/"` as that
user).
