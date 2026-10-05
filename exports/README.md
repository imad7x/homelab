# exports

Settings that only exist inside app databases, pulled from each app's API every night so
they are versioned too. Secrets in them are placeholders like everywhere else, and
volatile values (free space, last-run times) are dropped so the diffs stay meaningful.

| Folder | Contents |
|---|---|
| `radarr/`, `sonarr/` | quality profiles, custom formats, quality definitions, naming, media management, indexers, download clients, delay and release profiles, notifications, import lists, root folders, tags |
| `prowlarr/` | indexers, connected apps, download clients, proxies (FlareSolverr), tags, app profiles |
| `seerr/` | the discover page rows, including the custom sliders |
| `bazarr/` | language profiles |
| `jellyfin/` | installed plugins and versions; libraries |
| `pihole/` | blocklists, allow/deny domains, groups, clients |
| `grafana/` | datasources, folders, alert rules, contact points, notification policies |
| `cloudflared/` | the tunnel's hostname-to-service map |

They are a record to re-enter settings from, by hand or through each app's API; the
restore does not push them back.
