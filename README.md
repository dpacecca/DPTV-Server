# DPTV-Server

A self-hosted IPTV playlist manager and Xtream-Codes compatible server —
import live/VOD/series channels and EPG data from your provider(s), build
your own curated playlist(s) with a clean web UI, and share them back out
through DPTV-Server's own built-in XC server so any IPTV player (TiviMate,
IPTV Smarters, etc.) can connect directly.

Loosely inspired by IPTVBoss, rebuilt as a proper self-hosted web app
instead of a desktop tool: one server, a browser-based UI, and a database
instead of local files.

## What it does

- **Import** live, VOD, and series categories/channels from Xtream Codes
  API or M3U sources.
- **Build playlists**: select categories/channels from a source and drop
  them into your own categories; multi-select channels and **move to** /
  **copy to** another category; rename channels/categories; check
  "ignore name changes" per channel to stop provider renames overwriting
  your edits (off by default — provider renames win by default).
- **EPG mapping**: load one or more XMLTV guide URLs, auto-map channels by
  fuzzy name match (adjustable sensitivity) or map manually from a search
  list.
- **Dummy EPG**: for channels with no real guide, generate a schedule from
  the channel name itself (configurable program length), or parse embedded
  event date/times out of the name (e.g. `"Team A vs Team B 08/25 9:00PM"`)
  to schedule a single real-time event block.
- **Scheduled sync**: pick one or more times a day to re-sync sources and
  EPGs. New provider channels can auto-import into linked categories;
  channels removed by the provider auto-clear after a configurable grace
  period.
- **Its own XC server**: `player_api.php`, `get.php` (M3U), `xmltv.php`,
  and `/live/`, `/movie/`, `/series/` stream endpoints, authenticated by
  XC users you create (independent of the admin login). Streams are
  served as **pass-through 302 redirects** to the original provider URL —
  DPTV-Server never proxies the actual video, so it stays lightweight.
- **Duplicate quality scan**: scan a category's channels (probed via
  `ffprobe`) to detect actual resolution/framerate/bitrate, group channels
  that are the same feed at different qualities (e.g. "ESPN" / "ESPN HD" /
  "ESPN FHD"), and remove all but the best one — or tag the detected
  resolution into the channel name (e.g. `ESPN [1080p]`) instead of
  removing anything. Requires the `ffmpeg` package on the server (see
  Running it below).
- **EPG from iptv-org/epg**: browse [iptv-org/epg](https://github.com/iptv-org/epg)'s
  251 site scrapers by country, category, or specific channel search
  (using the real per-channel metadata from
  [iptv-org/database](https://github.com/iptv-org/database) where
  available) and add one as a normal, auto-refreshing EPG source — the
  server runs the scrape itself on a schedule. Country/category pulls
  are broad but slow; searching for and picking exactly the channels you
  actually use is far faster and lighter, and is the better choice once
  you know which ones matter to you. Optional; requires Node.js and a
  local checkout (see "Optional: iptv-org/epg scraper" below).
- **Automatic channel logos**: channels missing a logo from their provider
  get one automatically looked up from iptv-org's community-maintained
  logo database (matched via the mapped EPG channel id), refreshed daily
  in the background. Works independently of the scraper above — no setup
  needed. A logo set manually on a channel always takes priority.

## Architecture

- **Backend**: Python / FastAPI, SQLAlchemy 2.0 (async), PostgreSQL,
  Alembic migrations, APScheduler for the sync schedule.
- **Frontend**: React + TypeScript + Vite, Mantine UI.
- Single admin login for the management UI; separate XC users (username/
  password pairs) for player access, each enabled per playlist.

## Running it (Debian trixie or any Docker host)

```bash
cp .env.example .env
# edit .env: set DPTV_SECRET_KEY, DPTV_ADMIN_PASSWORD, and
# DPTV_PUBLIC_BASE_URL to http://<this-machine's-ip-or-domain>:8000
# (this is the URL your IPTV players will use)

docker compose up -d --build
```

- Web UI: `http://<host>:8080` (log in with `DPTV_ADMIN_USERNAME` /
  `DPTV_ADMIN_PASSWORD` from `.env`, default `admin` / `admin`).
- XC server / player endpoints: `http://<host>:8000` (also reachable
  through the UI's own origin at `8080`, since the frontend container
  proxies `/player_api.php`, `/get.php`, `/xmltv.php`, `/live/`,
  `/movie/`, `/series/` straight through to the backend).

Data (Postgres volume + generated output cache) persists in named Docker
volumes (`dptv-postgres`, `dptv-data`). Alembic migrations run
automatically on container start.

### First steps in the UI

1. **Sources** → Add Source (Xtream API recommended, or M3U) → click the
   refresh icon to load categories/channels.
2. Open the source, enable the categories you want available.
3. **EPG Sources** → Add EPG Source with an XMLTV URL → refresh.
4. **Playlists** → New Playlist → open it → **Import from Source** to pull
   channels into your own categories.
5. Click a channel to rename it, lock its name, map/auto-map its EPG, or
   configure dummy EPG.
6. **XC Users** → Add User → link it to the playlist(s) it should serve →
   open the link icon for the `player_api`/M3U/XMLTV URLs to paste into
   your IPTV player.
7. **Scheduler** → add one or more daily sync times, or hit "Sync Now".

## Running natively (no Docker) as a systemd service

This is the better fit for a Proxmox **LXC container** specifically:
Docker-in-LXC works but means nesting one container runtime inside
another for no benefit, since an LXC container already provides the
process/resource isolation Docker would otherwise be giving you. Running
the app's own processes directly, managed by systemd, is simpler and
avoids needing privileged/nesting container features at all.

```bash
# System packages
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql nginx nodejs npm git ffmpeg

# Database
sudo -u postgres psql -c "CREATE ROLE dptv LOGIN PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE dptv OWNER dptv;"

# App user + code
sudo useradd --system --create-home --shell /usr/sbin/nologin dptv
sudo git clone https://github.com/dpacecca/DPTV-Server.git /opt/DPTV-Server
sudo chown -R dptv:dptv /opt/DPTV-Server

# Backend
cd /opt/DPTV-Server/backend
sudo -u dptv python3 -m venv .venv
sudo -u dptv .venv/bin/pip install .

# Frontend (build once; the static output is what nginx serves)
cd /opt/DPTV-Server/frontend
sudo -u dptv npm install
sudo -u dptv npm run build
```

Edit `deploy/dptv-backend.service` (the DB password, `DPTV_SECRET_KEY`,
`DPTV_ADMIN_PASSWORD`, and `DPTV_PUBLIC_BASE_URL` → `http://<host-ip>:8000`),
then:

```bash
sudo cp /opt/DPTV-Server/deploy/dptv-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dptv-backend

# Serve the frontend + proxy the API/XC routes through the system's own nginx
sudo cp /opt/DPTV-Server/deploy/nginx-site.conf /etc/nginx/sites-available/dptv-server
sudo ln -s /etc/nginx/sites-available/dptv-server /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Web UI and XC server are both then reachable at `http://<host-ip>` (port
80). `frontend/nginx.conf` is the separate, Docker-specific config baked
into the `frontend` container image (it proxies to the `backend` service
by its Docker Compose hostname) — don't use it for a native install;
`deploy/nginx-site.conf` is the one written for this (it proxies to
`127.0.0.1:8000`, since here both processes share one host).

### Exposing only the XC server publicly (optional)

If you want IPTV players to reach the XC server from outside your LAN
while keeping the admin UI LAN-only, add a second nginx site from
`deploy/nginx-site-xc-only.conf` on its own port (8081 by default) that
proxies *just* the player endpoints (`player_api.php`, `get.php`,
`xmltv.php`, `live/`, `movie/`, `series/`) and 404s everything else —
install it alongside `nginx-site.conf`, not instead of it. Point a
reverse proxy or tunnel (e.g. a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
public hostname) at that port, and set `DPTV_PUBLIC_BASE_URL` to
whatever scheme/host actually reaches players from the outside (e.g.
`https://xc.yourdomain.com` when fronted by Cloudflare, even though this
nginx site itself only speaks plain HTTP internally — Cloudflare
terminates TLS at its edge).

### Optional: iptv-org/epg scraper

Lets you add an EPG source by picking countries or categories in the UI
instead of hunting down an XMLTV URL yourself. The server runs
[iptv-org/epg](https://github.com/iptv-org/epg)'s scrapers directly, so it
needs its own checkout with dependencies installed — it's not bundled,
since it's a separate, occasionally-updated project with 250+ site
scrapers you'd otherwise have to rebuild the whole app image just to
update. Skip this section if you're happy adding EPG sources by URL.

Requires Node.js >= 20.20. Debian's own `apt install nodejs` (used above
for building the frontend, which doesn't need anything this recent) can
land just under that — check with `node --version` first, and if it's
older, install a current one via NodeSource before cloning:

```bash
node --version   # if this prints < v20.20, run the two lines below first
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs

sudo -u dptv git clone https://github.com/iptv-org/epg.git /opt/DPTV-Server/iptv-org-epg
cd /opt/DPTV-Server/iptv-org-epg
sudo -u dptv npm install
```

`npm install`'s own `npm audit` may flag high-severity findings in `pm2`
(a transitive dependency of iptv-org/epg's own tooling, used for its
process-manager mode) - not reachable through anything DPTV-Server
invokes here (just `npm run grab`), so no action needed on those.

Then set `DPTV_IPTV_ORG_EPG_DIR=/opt/DPTV-Server/iptv-org-epg` — for a
native/systemd install, add it as another `Environment=` line in
`dptv-backend.service` (see the other variables there) and
`sudo systemctl restart dptv-backend`; for Docker, uncomment the
`DPTV_IPTV_ORG_EPG_DIR` line and volume mount in `docker-compose.yml`
(clone the checkout somewhere on the host first, same as above, and point
the volume at it) and `docker compose up -d --build`.

Country/category lists and channel logos come from
[iptv-org/database](https://github.com/iptv-org/database) (fetched
directly by the backend — no separate setup needed for that part; it's
what powers the automatic-logo feature above even without this scraper
configured). Grabbing a country/category can take a while since it's
scraping real broadcaster sites one by one — keep refresh intervals
reasonable.

### Updating a native install

`deploy/update.sh` runs the full update sequence for the native/systemd
setup above: `git pull`, reinstall backend deps, rebuild the frontend, then
`systemctl restart dptv-backend` (which also re-runs `alembic upgrade head`
via the service's `ExecStartPre`, so pending DB migrations are applied
automatically on restart — no separate migration step needed).

Run it directly:

```bash
sudo /opt/DPTV-Server/deploy/update.sh
```

Or install it once as a plain `update` command:

```bash
sudo ln -s /opt/DPTV-Server/deploy/update.sh /usr/local/bin/update
```

after which updating is just:

```bash
sudo update
```

## Project layout

```
backend/    FastAPI app, SQLAlchemy models, Alembic migrations, services
  app/models/       Source, Playlist, EPG, XC user, sync schedule tables
  app/services/     Xtream/M3U client, XMLTV parse/generate, sync engine,
                     fuzzy EPG mapper, dummy EPG generator
  app/api/routes/   Admin REST API + the public Xtream-Codes-compatible API
frontend/   React + Vite + Mantine admin UI
deploy/     systemd unit examples, update script for native installs
```

## Status / roadmap

Implemented: source/EPG import, M3U playlist import matched to an existing
source, playlist builder with move/copy/bulk edit, EPG auto/manual mapping,
dummy EPG (name + event-parsing, with a per-playlist library of custom
regex rules for naming conventions the built-in parser doesn't handle),
scheduled sync with auto-add/auto-remove, XC server with pass-through
streaming, XC user management, sync history, duplicate-channel quality
scanning (ffprobe-based resolution/framerate/bitrate detection,
keep-the-best dedup, resolution tagging into channel names), EPG sources
scraped from iptv-org/epg by country/category, automatic channel logos
from iptv-org's logo database.

Not yet built (lower priority for a self-hosted single-VM setup, since
IPTVBoss's cloud-sync/email features existed mainly to work around it
being a desktop app): email notifications, multi-device database sync.
Contributions/requests welcome.
