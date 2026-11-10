# Set up a server from the studio

The studio can turn a fresh VPS into a ConstellationCMS server by itself. No terminal, no
config files, no tokens.

## What you need

- A VPS running **Ubuntu 22.04 or newer** (24.04 is best) or **Debian 11 or newer**, with at
  least 512 MB of memory. The smallest size at Hetzner, DigitalOcean, Vultr or similar
  (around $5 a month) is plenty.
- Its **IP address** and **root password**, which the provider shows or emails when you create
  the server. (Or an SSH key already on this computer.)

## Steps

1. In the studio, open **Servers → Set up a new server**.
2. Enter the IP address and root password and press **Check this server**. The studio connects,
   looks at the system and lists, in plain words, what it will do. If something is in the way
   (an unsupported system, too little memory, another web server), it says so and stops.
3. Check the server's address. The suggestion (like `203-0-113-10.sslip.io`) is a free name that
   always points at your server, so it works straight away with no DNS setup. You can use your
   own instead (`cms.yourdomain.com`) after adding an A record for it.
4. Optionally tick **Also put a studio on the server**, to write from any browser or phone.
5. Press **Set it up** and wait a few minutes while it installs everything and shows progress:
   system packages, ConstellationCMS (the same version as your studio), the service, HTTPS from
   Let's Encrypt, firewall rules (when a firewall is on), and the optional studio.
6. When it says **All set**, go to **Servers** and press **Publish here** next to the sites to
   publish there. Then open each site's **Domain** page.

## What the studio keeps

- **Your root password is used once and never saved.** The studio puts an SSH key of its own on
  the server and uses that from then on.
- This computer is connected to the server with its own credential. The one-time installation
  key the server printed is retired straight away, because nobody saw it and nobody needs it.
  Other computers connect with pairing codes (Servers → Add another computer).
- If something fails, nothing is lost: run the setup again and it continues where it stopped.
  A server that is already set up is updated, keeping its sites and settings.

## Domains

Each site has a **Domain** page listing the DNS records to create where you bought the domain,
for example:

| Type | Name | Value |
|---|---|---|
| A | `example.com` | your server's IP address |
| A | `www.example.com` | your server's IP address |

The page checks every 15 seconds, using a public DNS service so your computer's cache doesn't
get in the way, and ticks each record as it starts working. It also spots common problems: a
leftover "parking" record, or Cloudflare's proxy (orange cloud) on a name Gemini needs. Once
everything is ticked, HTTPS turns on by itself.

## Already have a server with other things on it?

The setup can share a server with **Caddy**: it adds its own block to the Caddyfile, with a
backup. Other web servers (nginx, Apache) need the manual steps in the studio's **Guide**.
