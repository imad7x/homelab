# paperless - document archive

[Paperless-ngx](https://docs.paperless-ngx.com) 3.x with its own Postgres and Redis.
Web UI on `192.168.0.10:8000`, LAN and Tailscale only - it is deliberately not on the
tunnel.

| On the host | In the container | |
|---|---|---|
| `/data/inbox` | consume | drop folder; files are picked up within seconds (inotify works on the mergerfs pool) |
| `/data/documents/media` | media | originals and archived PDFs |
| `/data/documents/export` | export | target of `document_exporter` |
| `./data` | data | search index and classifier, on the NVMe |

- The drop folder is reachable over Samba as `\\lab7x\lab7x\inbox`. `inbox-share.smb.conf`
  is a dedicated `[inbox]` share that is written but not applied yet.
- OCR runs 2 workers x 1 thread so a bulk import cannot starve Jellyfin. Raise
  `PAPERLESS_TASK_WORKERS` for a big first import, then put it back.
- 3.x renamed settings: `PAPERLESS_CONSUMER_POLLING` is now
  `PAPERLESS_CONSUMER_POLLING_INTERVAL`, and `PAPERLESS_OCR_MODE=skip` is gone (`auto`
  does the same).

All settings are in `paperless.env` and `db.env`; see the `.example` files for the keys.
