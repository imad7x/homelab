# plants - plant photos with a care guide, a weather-aware watering plan and a feeding log

A small web app in plain Python (standard library plus Pillow for thumbnails) that turns
the plant photos in `~/plants` into a card grid, sorted by folder (Indoor, Outdoor,
Succulents). Each card opens a care sheet: common and botanical name, the plant's pot and
spot, how much to water and when, what to feed it (19:19:19, 00:52:34, vermicompost), how
much and how often, dos and don'ts, sunlight, temperature, lifespan, the size it can reach,
fun facts and pet safety. A Guide tab has the week's weather, the feeding plan per
fertiliser and the general dos and don'ts.

Web UI: `http://192.168.0.10:8087` on the LAN only (no tunnel route). It can be installed
as an app (manifest, icons and a service worker that keeps an offline copy), but browsers
only allow that on HTTPS.

## What it does

- **Inventory** - every folder under `~/plants` (mounted read-only at `/plants`) is a
  category and every photo in it a card. The file name is matched against the `match`
  names in `app/plants.json`, ignoring case and punctuation, so `Cat's Claw.jpg` finds
  `cats claw`. `Parijat 2.jpg` next to `Parijat.jpg` adds a second photo to the same
  card. A photo with no match still shows up, marked "Not in guide". The folder is read
  again on every page load, so new or renamed photos appear on refresh.
- **Care guide** - `app/plants.json`: one entry per plant with its pot (size, litres,
  saucer), spot (`sun`, `part` or `indoor`, and whether rain reaches it), water (ml per
  watering and `days`, the gap on a typical day), feeding (`npk`, `mkp`, `vermi` with dose,
  amount, interval and months) and dos/don'ts; plus the fertiliser descriptions, a
  getting-started plan and general dos and don'ts.
- **Watering** - Bangalore's weather swings within every season, so there are no fixed
  seasonal intervals. Each day since the last watering adds a drying factor from that
  day's reference evapotranspiration (ET0) against a typical day (4.5 mm): fully for pots
  in the sun, 70 % of the swing for part sun, 35 % indoors. The plant is due once the
  factors add up to its `days`. Days without weather data use the 2021-2025 monthly
  average (in `settings.et0_normal`). Cards show "Water today", "2 days overdue" or
  "Water in 4 days"; the drop button logs a watering.
- **Rain** - after a day with 8 mm or more, the page offers to count the rain as a
  watering for the open-air pots (`spot.rain`). It is never counted automatically: a
  city-wide figure can miss what fell on the balcony.
- **Feeding** - each fertiliser per plant has an interval and the months it applies.
  19:19:19 and 00:52:34 are kept at least two weeks apart, a liquid feed also logs that
  day's watering, and feeding is held on 35 °C days and before heavy rain (open pots).
- **Weather** - daily max/min temperature, rain, rain probability, ET0 and humidity for
  the last 30 days and the next 14 from Open-Meteo (no key), fetched every 3 hours and
  kept for 120 days in `state/weather.json`. Coordinates: `LAT`/`LON` (Bangalore centre by
  default); `WEATHER=off` disables it.
- **Thumbnails** - phone photos are 3-6 MB, so the server makes a 900 px copy for the
  grid and an 1800 px one for the detail view (EXIF rotation applied) in `state/cache/`,
  on first use and in the background after each scan. Copies for deleted photos are
  removed.

## API

| Call | Purpose |
| --- | --- |
| `GET /api/plants` | photo inventory, care guide, care log and weather |
| `GET /img/<key>/card.jpg`, `/full.jpg` | scaled copy of a photo; the key changes when the file does |
| `POST /api/log` | `{"plant", "kind", "date"}` or `{"entries": [...]}`: log a `water`, `rain`, `npk`, `mkp` or `vermi` |
| `POST /api/log/delete` | the same, to remove entries |
| `GET /api/ping` | health check |

POSTs need the header `X-Plants: 1` (no other site can send it without a CORS preflight,
which is never approved), dates can't be in the future, and every request must be
addressed to an IP, localhost or a name in `ALLOWED_HOSTS`.

## Files

- `app/server.py` - the server; `app/plants.json` - the care guide; `app/web/` - the page,
  the app manifest, the service worker and icons.
- `state/log.json` - the care log, `{plant: {kind: [dates]}}`.
- `state/weather.json` - the weather kept so far.
- `state/cache/` - thumbnails, safe to delete.

Code edits need `docker restart plants`; guide and page edits show on the next refresh.
