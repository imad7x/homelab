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
- **Watch** - the Huawei watch pairs with *Gadgetbridge* (no Huawei app or cloud), which
  writes to Android Health Connect; the phone app *HC Webhook* posts that data hourly to
  `POST /api/health` with an `X-Api-Key` header (the key and setup steps are on the Health
  tab). Payloads marked `"test": true` are checked but never stored. `GET /api/hc-webhook.json`
  hands the phone a file in HC Webhook's own settings-export format (URL, key, data types,
  resolutions, hourly sync) to import under Settings -> Settings Backup -> Import.
- **Access from outside** - the tunnel hostname (`PUBLIC_URL`) sits behind Cloudflare Access
  (email one-time PIN) like the other apps, with a second Access application that bypasses
  login for two paths only: `/api/health` (the phone's exporter can't log in; the endpoint
  checks its own key) and `/icons` (Android fetches the app icon without the login cookie).
- **Analysis** (`app/analysis.py`) - wear time per 15-minute slot (a heart-rate reading or
  steps); hours without the watch stay blank, a day's steps count toward averages only with
  10+ worn hours between 7:00 and 23:00, and a night only with 3+ hours of recorded sleep.
  Resting HR is the watch's value when one arrives, otherwise the lowest 30-minute average
  asleep. Recovery is a 0-100 score: resting HR and average HR asleep against the 30 nights
  before, last night's sleep, 3-night sleep debt against 7.5 h, and the last 3 days' TRIMP
  against the 28-day average (plus HRV against its 60-day range, if the watch ever sends it).
  "Rest" when resting HR is up 5+ bpm two nights running.
  Heart-rate zones and Edwards' TRIMP per workout, and WHO-style intensity minutes.
- **Reports and questions** (`app/ai.py`) - a weekly report every Monday from 06:30 and a
  monthly one on the 1st from 06:45, a 7-day report on demand, and free-text questions on
  the Health tab. Jobs queue in the app; a worker on the host (`ai/worker.py`, started
  `@reboot` from cron by `ai/start.sh`) long-polls `GET /api/ai/next` with the key in
  `state/worker.key`, runs the prompt and posts the answer back. Its provider is set in
  `ai/config.json`: an AI command-line tool logged in on the host, or the OpenAI Responses
  API with `OPENAI_API_KEY` in `ai/secrets.env`. Usage goes to `ai/state/usage.jsonl`.
- **Grafana** - the app rewrites `state/grafana/health.db` (a minute after new data, and
  every 15 minutes) for the *Health & Training* dashboard in the monitoring stack.

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
| `GET /api/reports`, `/api/reports/<id>` | reports with their statistics |
| `POST /api/reports` | report on the last 7 days now |
| `POST /api/ask`, `GET /api/ai/job/<id>` | queue a question, then poll for the answer |
| `GET /api/ai/next`, `POST /api/ai/result` | the host worker's calls, need `X-Worker-Key` |

POSTs from the page need the header `X-Gym: 1`, which browsers will not send cross-site
without a CORS preflight that this server never approves. Every request must be addressed
to an IP, localhost or a name in `ALLOWED_HOSTS`, which blocks DNS rebinding.

## Files

- `app/` - the code, mounted read-only (restart the container after an edit):
  `server.py` (HTTP), `store.py` (SQLite), `health.py` (watch data), `index.html`,
  `app.js`, `style.css`, `sw.js`, `program.json`, `icons/`, and `img/` (exercise photos
  from Free Exercise DB, public domain).
- `state/gym.db` - workouts, measurements, settings, watch data, AI jobs and both keys.
- `state/grafana/health.db` - Grafana's read-only copy, rebuilt from `gym.db`.
- `ai/` - the host worker: `worker.py`, `start.sh`, `config.json`, `state/` (log, usage).

Logs: `docker logs gym` (one line per sync and per batch of watch data).
