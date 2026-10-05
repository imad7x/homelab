# updater - one-click container updates

A small web app in plain Python (standard library only) that checks every container's
image against its registry and updates it with one click - a lighter alternative to
Portainer.

Web UI: `http://192.168.0.10:8084`. It holds the Docker socket and has no login, so it is
LAN-only and deliberately not on the tunnel. A Homepage tile shows how many updates are
pending.

## How it works

- **Check** - once a day at `CHECK_TIME` (06:00; set `CHECK_DAY=sun` to check weekly) it
  compares each running image's digest with the registry's using HEAD requests.
- **Update** - `docker compose pull` and `up -d --no-deps` for that one service, using the
  Compose labels the container was started with, then waits for its healthcheck (as long
  as the container's own healthcheck allows, at most 15 minutes).
- **Rollback** - if the new container does not come up healthy, the previous image is
  re-tagged and the service recreated. An old image is removed only once nothing uses it.
- **Drift note** - warns when a Compose file has changed since the container started,
  comparing the rendered config's hash with the container's label. A rollback reverts
  the image only, not Compose edits.
- **Release notes** - after each check, every waiting update gets the GitHub release notes
  between the running and the new version, expandable under its row. Lines that usually
  mean work for you ("breaking", "action required", "deprecated", "renamed", "no longer
  supported" ...) are listed above the notes, and a **possible breaking changes** badge
  shows on the row and in the *Update all* dialog. It is a word match, not a verdict.
  - The repository comes from the image's `org.opencontainers.image.source` label, or a
    short map in `notes.py` for images that don't name one (Firefly, Jellystat, cAdvisor,
    smartctl-exporter). Official images (postgres, redis, alpine) have none.
  - Versions are matched to release tags. Images without a version label (Grafana,
    Prometheus, cloudflared) are placed by build date: an image holds the newest release
    published before it was built. Pre-releases are skipped unless the new version is one.
  - LinuxServer images use their `docker-*` repository, whose releases carry both the
    image changes and the upstream app's changes.
  - GitHub is asked anonymously (60 requests an hour). Release lists are cached in
    `state/releases.json` for 6 hours and re-asked with their ETag. A failure only shows
    "couldn't fetch them" on that row; checks and updates never depend on it.

## API

| | |
|---|---|
| `GET /` | the page |
| `GET /api/status` | everything the page shows |
| `GET /api/summary` | counts for the Homepage widget |
| `GET /api/jobs/<id>` | one update job, with its log |
| `GET /api/notes/<name>` | release notes and flagged lines for one waiting update |
| `POST /api/check` | start a check now |
| `POST /api/update` | `{"containers": [...]}` or `{"all": true}`, optionally `"dry_run": true` |

Every POST needs the header `X-Updater: 1`; browsers will not send it cross-site without a
CORS preflight, which this server never approves.

## Files

- `app/` - the code, mounted read-only (restart the container after an edit):
  `server.py` (HTTP and background threads), `checks.py`, `registry.py`, `versions.py`,
  `dockerops.py`, `jobs.py`, `store.py`, `notes.py` (release notes), `index.html`.
- `state/` - `state.json` with the last check and the job history, `notes.json` with the
  release notes of waiting updates, and `releases.json` caching GitHub's release lists;
  runtime state, so it is not in git.
