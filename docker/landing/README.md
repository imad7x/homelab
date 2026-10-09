# landing

The public landing page for the bare domain: the public apps grouped into boxes with
their logos and a live online/offline dot, server vitals, media library counts, Pi-hole
totals, Bangalore weather with the chance of rain, and USD to INR (percentages only: no
core count, memory or disk sizes), plus a one-line "need access? drop your email" box in
the footer. The tunnel routes the bare domain to
port 3000. There is no login in front of it, so it is built to have nothing to give away.
Every page view is logged with the visitor's location, device and IP, and a LAN-only
dashboard shows them (see Visitor log).

| Container | Port | What |
|---|---|---|
| landing | 3000 | nginx (unprivileged) serving `site/` and one JSON file |
| landing-collector | - | writes `state/public/snapshot.json` every minute |
| landing-visitors | 3011 (LAN only) | visitor dashboard: visits per device, blocklist, weekly reports |

## How it is split

- **landing** only hands out files: the static page in `site/` and the snapshot. GET only,
  apart from the footer's email box (`POST /api/access`, which nginx writes to a log and
  answers from its own loopback; nothing else receives it), a strict Content-Security-Policy (no inline script or style; `/icons/` alone
  may style itself inline, which an SVG in an `<img>` needs and cannot abuse), read-only
  root filesystem, every capability dropped. It holds no keys.
- **landing-collector** has the keys (`collector.env`: Jellyfin API key, Navidrome
  user/salt/token) and no published port. Once a minute it reads Prometheus, Jellyfin,
  Navidrome, the Pi-hole digest (`monitoring/pihole-digest/digest.db`, read-only), Open-Meteo
  and Frankfurter (ECB rates), checks each public app's LAN health URL, and writes the
  snapshot. Visitors never reach it, so nothing they send can make the lab call anything.

Every field in the snapshot is assembled by hand in `collector/collect.py`; it carries no
LAN addresses, keys, titles or device names, only counts, percentages and public links.
Check it after a change with `curl -s localhost:3000/api/snapshot.json | grep 192.168`.

## Editing

- App tiles: `site/index.html` (static, so they work even if the collector is down), logos
  in `site/icons/` (from the dashboard-icons project; `gym.svg` is drawn here; a logo that
  fails to load falls back to the app's initial). Their status dots come from `SERVICES`
  in `collector/collect.py` (LAN health URL).
- Page: `site/index.html`, `site/style.css`, `site/app.js`. Both containers mount their
  code read-only: `docker restart landing-collector` picks up a collector change,
  `docker restart landing` an nginx change.
- **After editing `style.css`, `app.js` or `theme.js`, bump their versions** in
  `index.html`, or browsers keep the old file for hours (Cloudflare stretches browser
  caching of CSS and JS to 4 hours) and the page breaks against the new markup:

  ```sh
  cd site && for f in style.css app.js theme.js; do h=$(md5sum $f | cut -c1-10); sed -i -E "s#/$f(\?v=[0-9a-f]+)?\"#/$f?v=$h\"#" index.html; done
  ```

- Chart colours are validated for colour-blind separation against the card surfaces
  (`#ffffff` light, `#16181d` dark); keep the series colours if you add charts.
- SVG logos with long numeric ids fail the nightly sync's leak check; rename the ids.

## Visitor log

nginx writes one JSON line per page request that came in through Cloudflare to
`logs/visits-YYYY-MM.jsonl` (UTC month): time, IP, Cloudflare's location for that IP
(country, city, region, postcode, coordinates), user agent, the phone model and OS
version Chromium browsers send when asked (`Accept-CH` / `Critical-CH`), a device id, the
path, the referrer, the browser's languages and the Cloudflare data centre (from CF-Ray).
The open page's once-a-minute snapshot refresh goes to `logs/pings-YYYY-MM.jsonl`, which
gives each visit's "open for" time. The page also reports a few events to `/api/e`
(`logs/events-YYYY-MM.jsonl`): which app tile was tapped, which sections were reached,
leaving and returning to the tab, theme and table toggles. nginx logs only those shapes
(a regex on the query), rate-limited per IP; nothing typed on the page is sent. Emails left in the footer go to
`logs/requests-YYYY-MM.jsonl`. In-app browsers (Instagram, Facebook) name the exact
iPhone model and iOS version, which the dashboard turns into "iPhone 16 Pro Max · iOS 26.1". LAN requests,
the healthcheck, the page's own assets and its snapshot polling are not logged.

- **Device id:** page loads hand out a random first-party cookie (`vid`, HttpOnly,
  1 year), so visits are counted per browser even when the IP changes. A browser that
  never sends it back (a one-off visit, a private window, a bot) is grouped by IP + user
  agent instead.
- **Dashboard** (`landing-visitors`, port 3011, LAN only; never route it through the
  tunnel): visits by device and per day, unknown devices highlighted, a device list where
  you mark yours ("This is mine"), rename and block, and a panel per device (click it)
  with everything it did, as sessions: page loads and where they came from, sections
  reached, apps tapped, how long the page stayed open, emails, blocked attempts. Also
  recent visits / blocked attempts /
  bots and scanners, access requests, the blocklist and the weekly reports. It answers
  only the Host names in `ALLOWED_HOSTS`, and changes need its own header and origin.
- **Blocklist:** "Block" on a device blocks its id; a one-off visitor without one, or an
  IP or range typed into the Blocklist card, blocks the IP. Ranges wider than /16 (IPv6
  /32) and any IP your marked devices have used are refused, so you cannot lock yourself
  out from the dashboard. The dashboard writes `blocklist/ips.conf` and
  `blocklist/devices.conf`; `nginx/reload-on-blocklist.sh` (run by the image's entrypoint)
  reloads nginx within 5 seconds, and blocked visitors get "Not available" (403). Their
  attempts are still logged.
- **Weekly reports:** every Monday, for the week before (Monday to Sunday IST), into
  `visitors-state/reports/`. The SSH login brief (`lab-brief`) runs `visitors brief`,
  which says whether unknown devices visited since you last opened the dashboard or ran
  `visitors`, and when a new report is ready.
- **Command line** (`visitors/visitors.py`, linked as `visitors` in `~/.local/bin`):

  ```sh
  visitors              # latest visits: time, location, device, IP (bots hidden)
  visitors devices      # one line per device, with visit counts
  visitors requests     # emails from the footer box
  visitors report       # the latest weekly report
  visitors --all        # also bots, link previews, scanners and 404 probes
  ```

- Location is Cloudflare's estimate for the IP address (city level, the coordinates are
  the middle of that area), never where the device is; mobile networks can be off by
  hundreds of km. City, region, postcode and coordinates need the zone's managed
  transform "Add visitor location headers".
- The email box is rate-limited per visitor IP (6 a minute, burst 3) and needs a header
  only the page's script sends, so other sites cannot post to it. It only logs.
- `logs/` is written by nginx (uid 101, given group 1000 so it can write there);
  `visitors-state/` and `blocklist/` by the dashboard (uid 1000). None of it is published
  or backed up; delete old log months by hand.

## Restoring

`logs/`, `visitors-state/` and `blocklist/` are not in the repo. Before the first
`docker compose up -d`, create them so Docker does not make them root-owned:

```sh
mkdir -p logs blocklist visitors-state/reports && chmod 2775 logs && chmod 700 visitors-state
ln -sfn "$PWD/visitors/visitors.py" ~/.local/bin/visitors
```

## Rollback

`docker compose down` here and `docker compose up -d` in `monitoring/homepage` brings back
the old links-only Homepage on port 3000.
