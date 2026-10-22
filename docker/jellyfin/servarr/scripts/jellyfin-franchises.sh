#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  jellyfin-franchises.sh
#
#  Keeps franchise collections whole, in release order. Jellyfin's own
#  "add to collection" follows TMDb collections, which split the MCU into
#  Iron Man, Thor, Spider-Man (MCU) ... and leave Star Wars spin-offs out.
#  This adds every matching movie to one collection per franchise and sets
#  the collection's display order to release date.
#
#  A movie matches on its TMDb keywords (Jellyfin stores them as tags), its
#  TMDb collection id, or a title substring - see FRANCHISES below. Movies
#  are only ever added, never removed, so hand-added extras stay put.
#
#  Modes:
#    (no args)     sync every franchise
#    --dry-run     print what would change, change nothing
#
#  Config: franchises.env beside this script, or JF_URL/JF_KEY in the
#  environment. Cron runs it hourly (see crontab.snippet).
# ---------------------------------------------------------------------------
set -u

SELF_DIR=$(dirname "$0")
[ -f "$SELF_DIR/franchises.env" ] && . "$SELF_DIR/franchises.env"

export JF_URL="${JF_URL:-http://192.168.0.10:8096}"
export JF_KEY="${JF_KEY:-}"
[ -n "$JF_KEY" ] || { echo "ERROR: JF_KEY not set (see franchises.env)" >&2; exit 1; }

exec python3 - "$@" <<'PY'
import json, os, sys, time, urllib.parse, urllib.request

# name: the collection (created if missing). Any one rule matching is enough.
FRANCHISES = [
    {'name': 'Marvel Cinematic Universe',
     'tags': ['marvel cinematic universe (mcu)']},
    {'name': 'Star Wars Collection',          # the TMDb-made one already in the library
     'tmdb_collections': ['10'],
     'tags': ['star wars'],
     'title': 'star wars'},                   # Rogue One / Solo: A Star Wars Story
]

URL, KEY = os.environ['JF_URL'].rstrip('/'), os.environ['JF_KEY']
DRY = '--dry-run' in sys.argv[1:]


def api(path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers={
        'Authorization': f'MediaBrowser Token="{KEY}"', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M'), ('[dry-run] ' if DRY else '') + msg, flush=True)


def matches(movie, f):
    tags = {t.lower() for t in movie.get('Tags') or []}
    ids = movie.get('ProviderIds') or {}
    return (any(t in tags for t in f.get('tags', []))
            or ids.get('TmdbCollection') in f.get('tmdb_collections', [])
            or ('title' in f and f['title'] in movie['Name'].lower()))


# Collection contents are only listed with a user attached, so borrow an admin's view.
admin = next(u['Id'] for u in api('/Users') if u['Policy']['IsAdministrator'])
movies = api(f'/Items?userId={admin}&Recursive=true&IncludeItemTypes=Movie&CollapseBoxSetItems=false'
             '&Fields=Tags,ProviderIds,PremiereDate&enableImages=false')['Items']
boxsets = {b['Name']: b['Id'] for b in api(f'/Items?userId={admin}&Recursive=true&IncludeItemTypes=BoxSet')['Items']}

for f in FRANCHISES:
    want = sorted((m for m in movies if matches(m, f)), key=lambda m: m.get('PremiereDate') or '')
    if not want:
        continue
    cid = boxsets.get(f['name'])
    if cid is None:
        log(f"create '{f['name']}' with {len(want)} movies")
        if DRY:
            continue
        ids = ','.join(m['Id'] for m in want)
        cid = api(f"/Collections?Name={urllib.parse.quote(f['name'])}&Ids={ids}", 'POST')['Id']
        have = {m['Id'] for m in want}
    else:
        have = {i['Id'] for i in api(f'/Items?userId={admin}&ParentId={cid}')['Items']}
    missing = [m for m in want if m['Id'] not in have]
    if missing:
        log(f"add to '{f['name']}': " + ', '.join(m['Name'] for m in missing))
        if not DRY:
            api(f"/Collections/{cid}/Items?Ids={','.join(m['Id'] for m in missing)}", 'POST')
    # The metadata editor's "Display order"; the full item goes back so nothing else changes.
    item = api(f'/Items/{cid}?userId={admin}')
    if item.get('DisplayOrder') != 'PremiereDate':
        log(f"set '{f['name']}' display order to release date")
        if not DRY:
            item['DisplayOrder'] = 'PremiereDate'
            api(f'/Items/{cid}', 'POST', item)
PY
