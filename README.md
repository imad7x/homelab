# homelab

Everything that defines my home server, kept in git: every Docker Compose stack, the app
configs behind them, Grafana dashboards, Prometheus rules, scripts and host settings.
It is refreshed from the live machine every night.

**No secrets and no personal details are stored here.** Every password, API key, token,
hash and personal detail is replaced by a placeholder such as `[[secret:RADARR_APIKEY]]`
or `[[private:DOMAIN]]`. The real values, and the files that are private as a whole, are
kept encrypted - see [Secrets](#secrets).

## The machine

| | |
|---|---|
| Hardware | Lenovo ThinkCentre M720q Tiny · Intel i3-8100 · 16 GB RAM |
| Storage | NVMe boot SSD; two USB HDDs (`/mnt/disk1`, `/mnt/disk2`) pooled by mergerfs at `/data` |
| OS | Ubuntu, Docker Engine with Compose v2 |
| Network | LAN address `192.168.0.10`. Pi-hole is the LAN's DNS. Selected apps are published through a Cloudflare Tunnel, mostly behind Cloudflare Access; the rest are reached on the LAN or over Tailscale. |

## Layout

| Path | What it is |
|---|---|
| [`docker/`](docker) | Mirror of `/docker` on the server - one folder per Compose stack |
| [`host/`](host) | Host-level config: fstab and the mergerfs pool, Samba, Docker daemon, netplan, systemd, crontab, package list |
| [`exports/`](exports) | Settings that only live inside app databases, pulled from their APIs every night |
| [`tools/`](tools) | The sync tool that keeps this repo current |

## Stacks

| Stack | Apps | |
|---|---|---|
| `docker/pihole` | Pi-hole, DNS-over-HTTPS proxy, Cloudflare Tunnel, FlareSolverr | [README](docker/pihole) |
| `docker/jellyfin` | Jellyfin and Jellystat; the *arr stack (`servarr/`); music (`music/`) | [README](docker/jellyfin) |
| `docker/monitoring` | Prometheus, Grafana, node-exporter, cAdvisor, Scrutiny, smartctl-exporter, Homepage | [README](docker/monitoring) |
| `docker/finance` | Firefly III with a mail-driven ingest worker | [README](docker/finance) |
| `docker/paperless` | Paperless-ngx document archive | [README](docker/paperless) |
| `docker/updater` | One-click container updater (custom app) | [README](docker/updater) |
| `docker/gym` | Workout program, set logger and watch data (custom app) | [README](docker/gym) |
| `docker/landing` | Public landing page: app launcher, live vitals, weather and rates (custom app) | [README](docker/landing) |
| `docker/homeassistant` | Home Assistant | [README](docker/homeassistant) |

### Containers

Generated from `docker ps` on every sync.

<!-- inventory:start -->
| Stack (`docker/…`) | Container | Image | Host ports |
|---|---|---|---|
| finance | finance-db | `postgres:16-alpine` | 5432 |
| finance | finance-ingest | `finance-ingest:latest` | - |
| finance | firefly | `fireflyiii/core:latest` | 3002 |
| finance | firefly-cron | `alpine:3` | - |
| gym | gym | `gym:latest` | 8086 |
| homeassistant | homeassistant | `ghcr.io/home-assistant/home-assistant:stable` | - |
| jellyfin | jellyfin | `lscr.io/linuxserver/jellyfin:latest` | 1900, 7359, 8096 |
| jellyfin | jellystat | `cyfershepard/jellystat:latest` | 3003 |
| jellyfin | jellystat-db | `postgres:16-alpine` | - |
| jellyfin/discover | jellylook | `jellylook-local:latest` | 3045 |
| jellyfin/discover | tmdb-via-seerr | `tmdb-via-seerr:latest` | - |
| jellyfin/music | navidrome | `deluan/navidrome:latest` | 4533 |
| jellyfin/music | slskd | `slskd/slskd:latest` | 5030, 50300 |
| jellyfin/servarr | bazarr | `lscr.io/linuxserver/bazarr:latest` | 6767 |
| jellyfin/servarr | jellyseerr | `ghcr.io/seerr-team/seerr:latest` | 5055 |
| jellyfin/servarr | prowlarr | `lscr.io/linuxserver/prowlarr:latest` | 9696 |
| jellyfin/servarr | qbittorrent | `lscr.io/linuxserver/qbittorrent:latest` | 6881, 8080 |
| jellyfin/servarr | radarr | `lscr.io/linuxserver/radarr:latest` | 7878 |
| jellyfin/servarr | sonarr | `lscr.io/linuxserver/sonarr:latest` | 8989 |
| landing | landing | `nginxinc/nginx-unprivileged:alpine` | 3000 |
| landing | landing-collector | `landing-collector:latest` | - |
| landing | landing-visitors | `python:3.13-alpine` | 3011 |
| monitoring | cadvisor | `gcr.io/cadvisor/cadvisor:v0.49.1` | 8082 |
| monitoring | grafana | `grafana/grafana` | 3001 |
| monitoring | node-exporter | `prom/node-exporter:latest` | - |
| monitoring | prometheus | `prom/prometheus` | 9090 |
| monitoring | scrutiny | `ghcr.io/analogj/scrutiny:v0.9.4-omnibus` | 8083 |
| monitoring | smartctl-exporter | `quay.io/prometheuscommunity/smartctl-exporter:v0.14.0` | 9633 |
| monitoring/homepage-lan | homepage-lan | `ghcr.io/gethomepage/homepage:latest` | 3010 |
| paperless | paperless | `ghcr.io/paperless-ngx/paperless-ngx:latest` | 8000 |
| paperless | paperless-db | `postgres:16-alpine` | - |
| paperless | paperless-redis | `redis:7-alpine` | - |
| pihole | cloudflared | `cloudflare/cloudflared:2025.10.0` | - |
| pihole | cloudflared_tunnel | `cloudflare/cloudflared:latest` | - |
| pihole | flaresolverr | `ghcr.io/flaresolverr/flaresolverr:latest` | 8191 |
| pihole | pihole | `pihole/pihole:latest` | 53, 8081 |
| updater | updater | `updater:latest` | 8084 |
<!-- inventory:end -->

## How the repo stays current

`tools/sync.sh` runs from cron every night at 04:17. It:

1. copies an allow-list of config files from `/docker` and the host - never databases,
   media, caches or logs - and pulls the database-only settings into `exports/`;
2. replaces secrets and personal details with placeholders, and checks that every
   redacted file turns back into exactly the live file;
3. runs the leak checks: every known secret value, a list of personal terms, e-mail
   addresses, private keys, blocked words, and [gitleaks](https://github.com/gitleaks/gitleaks);
4. only if all of that passes, commits, pushes, and re-uploads any secret bundle whose
   contents changed.

If a check fails, nothing is committed or pushed and the reason is in
`~/.local/state/homelab-sync.log`. Run it by hand with `tools/sync.sh`; for a dry run that
writes the files and runs the checks without committing, use
`python3 tools/homelab_sync.py sync`.

## Secrets

- Real values and wholly private files (`.env` files, the finance ingest code, Home
  Assistant's `.storage`, ...) are packed per stack into a `tar.gz`, **encrypted with
  [age](https://age-encryption.org) on the server**, and stored as this repository's
  GitHub Actions secrets `BUNDLE_<STACK>_<NN>` (in chunks of 45 KB). GitHub only ever
  holds ciphertext.
- The age key is `~/.config/homelab-backup/age.key` on the server and **is also kept
  offline** - without it the bundles cannot be opened.
- The sync tool's own private settings (personal terms, blocked words) live next to the
  key in `~/.config/homelab-backup/`, never in this repo.

## Restoring

1. On the new machine install Docker, git, [gh](https://cli.github.com) and
   [age](https://github.com/FiloSottile/age), then `gh auth login`.
2. `git clone https://github.com/imad7x/homelab ~/github/homelab && cd ~/github/homelab`
3. Fetch the encrypted bundles: `gh workflow run export-secrets.yml`, wait for it to
   finish, then `gh run download -n homelab-bundles -D /tmp/bundles`.
4. `python3 tools/homelab_sync.py restore /tmp/bundles --identity /path/to/age.key --target /docker`
   fills in every placeholder and puts the private files back. Host files land in
   `/docker/_host/`, to be copied into place with sudo.
5. Create what the compose files expect to exist: `docker volume create monitoring_prometheus_data`
   and `docker volume create monitoring_grafana_data`. Start `finance` and `jellyfin`
   before `monitoring`, because Grafana joins their networks.
6. `docker compose up -d` in each stack, `pihole` first.

Databases, media, documents and metrics history are data, not configuration, and are not
in this repo.
