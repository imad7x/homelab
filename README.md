# homelab

My home server as code: Docker Compose stacks, configs, Grafana dashboards and host settings.

| Stack | What runs there |
|---|---|
| [`pihole`](docker/pihole) | Pi-hole, DNS-over-HTTPS, Cloudflare Tunnel |
| [`jellyfin`](docker/jellyfin) | Jellyfin, the *arr stack, music |
| [`monitoring`](docker/monitoring) | Prometheus, VictoriaMetrics, Grafana, Homepage |
| [`homeassistant`](docker/homeassistant) | Home Assistant |

My own apps live in their own repos: [movies](https://github.com/imad7x/movies),
[resume-web](https://github.com/imad7x/resume-web),
[updater](https://github.com/imad7x/updater), [lgtv](https://github.com/imad7x/lgtv),
[x7-home](https://github.com/imad7x/x7-home). A few more.

`tools/` holds the nightly sync and its leak checks; `tools/homelab_sync.py restore` rebuilds
a machine from this repo plus the encrypted bundles.
