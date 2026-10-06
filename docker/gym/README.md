# gym - workout program, logger and watch data

A phone-first web app in plain Python (standard library only, SQLite) for training at the
gym: a 6-day push/pull/legs program with a guide for every exercise, a set logger that
suggests the next weight, progress charts, body measurements, and the Huawei watch's
health data.

Web UI: `http://192.168.0.10:8086` on the LAN. For use at the gym it is meant to go on the
tunnel under its own hostname behind Cloudflare Access, like Firefly (that hostname is the
`ALLOWED_HOSTS` entry in `compose.yaml`). A Homepage tile shows days trained this week and
the streak.

## What it does

- **Program** - `app/program.json`: Push A, Pull A, Legs A, Push B, Pull B, Legs B on
  Monday to Saturday, Sunday off. Each muscle is trained twice a week at about 10-12 hard
  sets. Weeks 1-2 are a lighter "learn" phase, and every 8th week is a deload. Every
  exercise has photos, setup, cues, common mistakes and swaps for when a machine is taken.
  The rationale, nutrition and measuring notes and the sources are on the Plan tab.
- **Logger** - starts today's session with each exercise's suggested weight. The
  suggestion uses double progression: top of the rep range on every set means the next
  weight up, two weak sessions in a row mean a 10% reset. The logger also shows warm-up
  sets, starts a rest timer when a set is ticked, flags personal bests, and has a
  "short on time" mode that keeps only the first two lifts. Cult classes are logged with
  one tap and count toward the week.
- **Offline** - a service worker keeps the page and the program on the phone, and every
  change is saved locally first and synced when the server answers. Each record carries
  the time it was last changed and the newer copy wins, so a phone that was offline can't
  overwrite later edits.
- **Progress** - a 12-week consistency grid, the streak in weeks that met the goal
  (5 of 6 by default), estimated 1-rep-max charts for the main lifts, and hard sets per
  muscle this week against the plan.
- **Body** - weigh-ins with a 7-day average, waist, and InBody scans.
- **Watch** - the phone app *HC Webhook* posts Android Health Connect data to
  `POST /api/health` with an `X-Api-Key` header. The data gets there either from Huawei
  Health through *Health Sync*, or from Gadgetbridge for a setup with no Huawei cloud. The
  page shows steps, sleep, resting HR and HRV, the watch's own workouts, and the heart rate
  during each logged session. The setup steps and the key are on the Watch tab.

## API

| | |
|---|---|
| `GET /api/state` | every saved record plus the watch data, for the page |
| `GET /api/summary` | days this week, goal, streak, last workout (Homepage widget) |
| `GET /api/export` | everything as one JSON download |
| `GET /api/export.csv` | every logged set as CSV |
| `POST /api/sync` | `{"docs": [...]}` records changed on a device |
| `POST /api/token` | make a new key for the watch-data exporter |
| `POST /api/health` | HC Webhook JSON payload, needs `X-Api-Key` |

POSTs from the page need the header `X-Gym: 1`, which browsers will not send cross-site
without a CORS preflight that this server never approves. Every request must be addressed
to an IP, localhost or a name in `ALLOWED_HOSTS`, which blocks DNS rebinding.

## Files

- `app/` - the code, mounted read-only (restart the container after an edit):
  `server.py` (HTTP), `store.py` (SQLite), `health.py` (watch data), `index.html`,
  `app.js`, `style.css`, `sw.js`, `program.json`, `icons/`, and `img/` (exercise photos
  from Free Exercise DB, public domain).
- `state/gym.db` - workouts, measurements, settings, watch data and the exporter key.

Logs: `docker logs gym` (one line per sync and per batch of watch data).
