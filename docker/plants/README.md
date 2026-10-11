# plants - plant photos with a care guide and a watering log

A small web app in plain Python (standard library plus Pillow for thumbnails) that turns
the plant photos in `~/plants` into a card grid, sorted by folder (Indoor, Outdoor,
Succulents). Each card opens a care sheet: common and botanical name, a check of the
nursery's name, sunlight, temperature, watering amount and frequency per season, lifespan,
the size it can reach, care tips, feeding, fun facts and pet safety.

Web UI: `http://192.168.0.10:8087` on the LAN only (no tunnel route).

## What it does

- **Inventory** - every folder under `~/plants` (mounted read-only at `/plants`) is a
  category and every photo in it a card. The file name is matched against the `match`
  names in `app/plants.json`, ignoring case and punctuation, so `Cat's Claw.jpg` finds
  `cats claw`. `Aglaonema 2.jpg` next to `Aglaonema.jpg` adds a second photo to the same
  card. A photo with no match still shows up, marked "Not in guide". The folder is read
  again on every page load, so new or renamed photos appear on refresh.
- **Care guide** - `app/plants.json`, one entry per plant. `id_check` records whether the
  nursery's name was confirmed, refined (right genus, more precise name), corrected or
  identified from an unnamed photo. `placement` says where the plant does best; a card is
  flagged "Better indoors/outdoors" when its photo sits in the other folder.
- **Watering** - each plant has an interval per season (summer Mar-May, monsoon Jun-Oct,
  winter Nov-Feb, set for Bangalore). The drop button on a card or "I watered it today" in
  the detail view logs a date; cards then show "Water today", "2 days overdue" or "Water
  in 4 days". Past dates can be added and wrong ones removed. The log is keyed by plant,
  not folder, so moving a photo between indoor and outdoor keeps its history.
- **Thumbnails** - phone photos are 3-6 MB, so the server makes a 900 px copy for the
  grid and an 1800 px one for the detail view (EXIF rotation applied) in `state/cache/`,
  on first use and in the background after each scan. Copies for deleted photos are
  removed.

## API

| Call | Purpose |
| --- | --- |
| `GET /api/plants` | photo inventory, the care guide and the watering log |
| `GET /img/<key>/card.jpg`, `/full.jpg` | scaled copy of a photo; the key changes when the file does |
| `POST /api/water` | `{"plant", "date"}` log a watering |
| `POST /api/water/delete` | `{"plant", "date"}` remove one |
| `GET /api/ping` | health check |

POSTs need the header `X-Plants: 1` (no other site can send it without a CORS preflight,
which is never approved), and every request must be addressed to an IP, localhost or a
name in `ALLOWED_HOSTS`.

## Files

- `app/server.py` - the server; `app/plants.json` - the care guide; `app/web/` - the page.
- `state/watering.json` - the watering log.
- `state/cache/` - thumbnails, safe to delete.

Code edits need `docker restart plants`; guide and page edits show on the next refresh.
