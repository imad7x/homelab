# movies - what to watch next, ranked by IMDb rating and the films you love

A small web app in plain Python (standard library only) with a SQLite catalogue of every
film that has 1,000 or more IMDb votes (about 49,000). "For you" ranks films like the
ones marked loved, weighted by their IMDb rating; "Explore" has ready-made lists and
filters; every film shows whether it is in Jellyfin and where it streams in India, with
a "Get it" button that requests it in Seerr.

Web UI: `http://192.168.0.10:8089` on the LAN only (no tunnel route, no login of its own).

## Where the data comes from

- **IMDb ratings** - IMDb's own daily files (`title.ratings` and `title.basics` from
  datasets.imdbws.com, free for personal use), fetched weekly. Films only, no adult titles.
  Rating, vote count, year, runtime and the IMDb genres come from here.
- **Everything else** - Seerr already talks to TMDb, so the app asks Seerr: one search for
  the IMDb id (`imdb:tt...`) gives the TMDb id, then one details call gives the poster,
  plot, tagline, TMDb keywords, director, writers, cast, original language, certificate,
  trailer and the streaming / rent / buy services for `REGION` (IN). Films are filled in
  most-voted first (3,000+ votes and rating 5.5+, about 22,000 films, three at a time;
  the first pass takes about 75 minutes) and refreshed every 45 days so streaming info
  stays current. A film opened in the app is filled in on the spot if needed.
- **Library** - Seerr's media list (every 10 minutes) says which films are in Jellyfin
  (with a Play link) or requested.
- **Played films (optional)** - with `JELLYFIN_API_KEY` in `movies.env`, films that
  `JELLYFIN_USER` has played are marked seen (no verdict) every 10 minutes.

## How "For you" ranks

Every filled-in film is described by its TMDb keywords, IMDb genres, director, writers,
top-billed cast, original language and decade. Similarity to a marked film:

| Part | Weight | How |
|---|---|---|
| Keywords | 0.50 | tf-idf cosine; rare keywords count more, mood tags (TMDb's "suspenseful", "inspirational"...) half |
| Genres | 0.22 | weighted overlap of the IMDb genres |
| People | 0.12 | same director, writer or lead actors |
| Language | 0.08 | same original language |
| Era | 0.08 | release years within 30 years, closer counts more |

Keyword noise is dropped (`duringcreditsstinger`, `woman director`, `sequel`,
`based on novel or book`...) and near-duplicates are folded into one (`biography`,
`based on true story`, `historical figure` -> `true story`), so one theme can't count
four times. Each marked film's closest match scores 1. Marks weigh: loved 1.0,
watchlist 0.5, seen and fine 0.4, seen without a verdict 0.2; "not for me" pushes
look-alikes down. TMDb's own "recommended" list for each loved film adds a small bonus.

Final score = 0.6 x taste + 0.4 x quality, where quality is the IMDb rating weighted by
votes (Bayesian, 10,000 votes at 6.8 as the prior), so a 9.0 from 2,000 votes counts for
less than an 8.6 from 900,000. The card shows what the film shares with the loved one.

## Using it

- **For you** - the loved films sit on top (add more with *Add*: tap posters of famous
  films, search, or paste a list of titles). Filters: minimum IMDb rating (7+ by
  default), where (anywhere, watch now = Jellyfin or one of your services, Jellyfin,
  your services), language, genre.
- **Explore** - lists: IMDb Top (25,000+ votes, weighted), Hidden gems (7.5+, under
  40,000 votes), New & good (last two years), Indian cinema, True stories,
  Mind-benders, Spies & codebreakers, Heists & cons, Courtroom, Space, Everything.
  Filters for genre, decade, rating, language, where, sort, under 2 h, and little-known
  films (1,000+ votes instead of 3,000+). A tag on a film page opens every film with it.
- **Watchlist** / **Seen** - seen films carry a verdict (Loved it / Fine / Not for me);
  hidden films ("not interested") are listed at the bottom of Seen and can be brought back.
- **Settings** (gear) - tick the streaming services you pay for; shows fill progress.
- **Get it** asks twice, then sends `POST /api/v1/request` to Seerr with its API key
  (requests go in as that key's user) and Radarr downloads it.
- `SEED_LOVED` (default The Imitation Game) is marked loved once, on the first start.

## Files

| Path | What |
|---|---|
| `app/server.py` | HTTP server and API |
| `app/catalog.py` | IMDb import, Seerr fill-in, library and Jellyfin sync, SQLite schema |
| `app/recommend.py` | similarity, ranking, "more like this" |
| `app/web/` | the page (vanilla JS, light and dark) |
| `movies.env` | `SEERR_API_KEY`, optional `JELLYFIN_API_KEY` |
| `state/movies.db` | catalogue, marks, settings, cached TMDb lists |

The code is mounted read-only into the container, so an edit needs `docker restart
movies`, not a rebuild. After editing `app.js` or `style.css`, bump `?v=` in
`web/index.html`.

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/picks` | `?min= &where= &lang= &genre= &offset= &limit=` |
| GET | `/api/explore` | `?list= &genre= &era= &min= &lang= &where= &kw= &short= &all= &sort= &unseen=` |
| GET | `/api/search?q=` | title search, a trailing year narrows it |
| GET | `/api/movie/<imdb id>` | one film with "more like this" |
| GET | `/api/marks?list=` | `watchlist`, `seen` or `hidden` |
| GET | `/api/status`, `/api/settings` | fill progress, job state, services |
| POST | `/api/mark` | `{"id", "list": watchlist/seen/hidden/none, "verdict": 2/1/-1/null}` |
| POST | `/api/match` | `{"text"}` one title per line |
| POST | `/api/request` | `{"id"}` request in Seerr |
| POST | `/api/settings` | `{"services": [...]}` |

POSTs need the header `X-Movies: 1`, and the Host must be an IP, localhost or a name in
`ALLOWED_HOSTS`.
