# jellyfin - media

Four Compose projects; the first three share `/data` (the mergerfs pool):

| Folder | Project | Apps |
|---|---|---|
| `.` | `jellyfin` | Jellyfin (Intel Quick Sync through `/dev/dri`), Jellystat and its Postgres |
| `servarr/` | `servarr` | qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr, Seerr, on `servarrnetwork` (172.39.0.0/24, fixed IPs) |
| `music/` | `music` | Navidrome, slskd (Soulseek) |
| `discover/` | `discover` | Jellylook and the small service that answers its TMDb lookups from Seerr |

| App | Port | App | Port |
|---|---|---|---|
| Jellyfin | 8096 | Sonarr | 8989 |
| Jellystat | 3003 | Radarr | 7878 |
| Seerr | 5055 | Bazarr | 6767 |
| qBittorrent | 8080, torrents on 6881 | Prowlarr | 9696 |
| Navidrome | 4533 | slskd | 5030, Soulseek on 50300 |
| Jellylook | 3045 | | |

## How releases are picked: seeders first

New movies and series grab 720p **or** 1080p, whichever release has the most seeders.

- Radarr and Sonarr profile **Any** is a single quality group "720p/1080p" (HDTV, WEB-DL,
  WEBRip and Bluray at 720p and 1080p, no remux). Grouped qualities rank equal and every
  custom format scores 0, so the seeder count decides. Upgrades are off and
  propers/repacks are set to "do not prefer", so a repack cannot outrank seeders.
- Blockers score -10000 against a minimum of 0: movies over 5 GB on the 720p/1080p
  profiles; on Ultra-HD, 4K SDR over 12 GB and anything over 25 GB. 4K is only grabbed
  when Ultra-HD is picked explicitly in a Seerr request.
- Radarr's profile language is *Original*, so non-English releases are not rejected.
- Seerr requests default to **Any**; Sonarr is the default server.
- qBittorrent caps uploads at 6 MB/s and refuses executable file types (`*.exe`, `*.lnk`,
  `*.scr`, `*.bat`, ...) - public trackers push fake pre-air releases.
- Prowlarr feeds both apps; indexers behind Cloudflare go through FlareSolverr (tag
  `flare`, in the `pihole` stack).
- Radarr, Sonarr and Prowlarr only answer the host names in their *Allowed Hosts*; add a
  new name or IP there (or to `<AllowedHosts>` in `config.xml`) before using it.

Profiles, custom formats and the other settings that live in the apps' databases are
exported nightly to [`exports/radarr`](../../exports/radarr),
[`exports/sonarr`](../../exports/sonarr) and [`exports/prowlarr`](../../exports/prowlarr).
Seerr's custom discover rows (new movies and series on streaming services, including Hindi
rows) are in [`exports/seerr`](../../exports/seerr).

## Subtitle pre-warming

`servarr/scripts/jellyfin-prewarm.sh` makes Jellyfin extract embedded subtitle tracks and
font attachments ahead of time. A cold extraction costs 4-5 s before the first frame, and
again on every subtitle switch. Cron runs it with `--all` every 30 minutes and it skips
items that are already warm; it also works as a Sonarr/Radarr *Custom Script* on import.
Settings are in `servarr/scripts/prewarm.env`.

## Jellystat and the Media Usage dashboard

Jellystat (port 3003) records who watches what through Jellyfin's websocket and ignores
plays shorter than 30 s. `jellystat/report.sql` adds `report.*` SQL functions that the
Grafana **Media Usage** dashboard reads as a read-only role; re-apply it with `psql` after
editing it.

## Music

Navidrome serves `/data/music` read-only and rescans every 15 minutes. slskd downloads
into the same folder.

## Jellyfin plugins and libraries

Installed plugins and their versions: [`exports/jellyfin/plugins.json`](../../exports/jellyfin/plugins.json).
Plugin settings: `config/data/plugins/configurations/`. Library options:
`config/data/root/default/*/options.xml`.

## What to watch next: Jellylook (`discover/`)

[Jellylook](https://github.com/dean1850/jellylook) (port 3045) suggests titles from each
viewer's own Jellyfin history, read through Jellystat. Tick *who's watching* and press
*Scan*: one AI call returns about 60 titles, each with IMDb and TMDb ratings, a trailer,
where it streams in India, and an *Add to Seerr* button. The filters include a minimum
IMDb rating. Likes (♡) and hides (✕) are kept per viewer and steer that viewer's next
scan. Requests go out under the Seerr API key's account (the shared one). It has no login,
so it is LAN and Tailscale only.

- **AI:** Gemini's free tier, `gemini-3.5-flash-lite`. The full flash models spend part
  of Jellylook's 8,000-token output limit on thinking and cut the 60-title answer short;
  the newest one is also often over capacity on the free tier.
- **No TMDb key:** Jellylook only talks to TMDb with an API key. `tmdb-via-seerr` answers
  the few TMDb v3 calls it makes (search, details, trailers, watch providers) from Seerr's
  API instead, and the locally built `jellylook-local` image is the upstream build with its
  TMDb address pointed there. Both come from the stack's `Dockerfile`; the upstream build
  is pinned by tag because the patch edits one line of its source, so move the tag on
  purpose and run `docker compose build` when updating.
- **Keys** (`jellylook.env`): dedicated `jellylook` keys in Jellyfin and Jellystat, the
  Seerr API key, the Gemini key and a free OMDb key for IMDb ratings (1,000 lookups a day).
