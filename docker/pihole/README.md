# pihole - DNS, tunnel and FlareSolverr

| Service | Address | What |
|---|---|---|
| `pihole` | 53 tcp/udp, web UI on 8081 | the LAN's DNS and ad blocker (Pi-hole v6, configured in `etc-pihole/pihole.toml`) |
| `cloudflared` | 172.20.0.3:5053 | DNS-over-HTTPS proxy that Pi-hole forwards to (Google and Quad9 upstreams) |
| `cloudflared_tunnel` | - | Cloudflare Tunnel that publishes selected apps; ingress is managed in the Zero Trust dashboard |
| `flaresolverr` | 8191 | solves Cloudflare challenges for Prowlarr's indexers |

All four sit on `pihole_net` (172.20.0.0/24) with fixed IPs. The *arr apps use Pi-hole
(`172.20.0.2`) as their DNS.

- **Tunnel**: its token is `CLOUDFLARE_TUNNEL_TOKEN` in `.env`. The hostname-to-service
  map is read from the tunnel's log every night into
  [`exports/cloudflared/tunnel-ingress.json`](../../exports/cloudflared/tunnel-ingress.json).
  Apps without a login strong enough for the internet (the updater, Paperless) are kept
  off the tunnel on purpose; reach those over Tailscale.
- **Blocklists, groups and allow/deny lists** live in Pi-hole's database and are exported
  to [`exports/pihole`](../../exports/pihole).
- At boot, `pihole-pw.service` runs `host/home/pihole-pw.sh`, which calls
  `pihole setpassword`.
- There are no local DNS overrides: at home, apps are opened as `192.168.0.10:<port>`.
