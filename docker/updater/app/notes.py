"""Release notes for waiting updates, and a warning when they look risky.

For every container with an update waiting, the notes between the version it
runs and the version on offer come from the project's GitHub releases:

  1. Which repository: an explicit map for images whose labels don't name one
     (or name somewhere other than GitHub), then the image's source label.
  2. Which releases: the running and the new version are looked up among the
     release tags. An image without a version label (grafana, prometheus,
     cloudflared ...) is placed by its build date instead: it holds the newest
     release published before it was built. Everything newer than the running
     one, up to and including the new one, is shown. Pre-releases are left out
     unless the new version is one.
  3. Every line of those notes is scanned for wording that usually means work
     for whoever updates: "breaking", "action required", "deprecated",
     "renamed", "no longer supported" ... Those lines are listed above the
     notes and the page shows a "possible breaking changes" badge. It is a
     word match, so it can cry wolf; it is a prompt to read, not a verdict.

GitHub is asked anonymously (60 requests an hour per IP). Each release list is
cached in releases.json next to state.json and asked again with its ETag, which
GitHub answers with a 304 that costs nothing when nothing changed. Notes are
only looked up for containers with an update waiting, in the background after a
check, and any failure only means "couldn't fetch notes" for that container: it
never touches the check or an update.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import registry
import store
import versions

API = "https://api.github.com"
TIMEOUT = 20             # seconds, per request
PER_PAGE = 100           # GitHub's maximum
MAX_PAGES = 3            # LinuxServer repos publish a development build almost daily
FRESH = timedelta(hours=6)       # a release list younger than this is used without asking
RECHECK = timedelta(hours=12)    # notes for an unchanged pending update are rebuilt this often
SLACK = timedelta(hours=6)       # an image can be built a little before its release is published
MAX_RELEASES = 20        # releases kept per container (the count still says how many there were)
MAX_BODY = 20_000        # characters of one release's notes
MAX_FLAGGED = 12         # flagged lines kept per container
PENDING = ("update", "restart")

STATE_DIR = os.path.dirname(store.PATH)
NOTES_PATH = os.path.join(STATE_DIR, "notes.json")
CACHE_PATH = os.path.join(STATE_DIR, "releases.json")

# Image repository (as registry.parse_ref names it) -> GitHub "owner/repo", for
# images whose labels don't say, or point somewhere other than GitHub. None
# means "no release notes on GitHub": the official Docker images are built
# from Docker's own repos, whose releases say nothing about the program.
REPOS = {
    "fireflyiii/core": "firefly-iii/firefly-iii",        # label points to Azure DevOps
    "cyfershepard/jellystat": "CyferShepard/Jellystat",  # no labels
    "cadvisor/cadvisor": "google/cadvisor",              # gcr.io, no labels
    "prometheuscommunity/smartctl-exporter": "prometheus-community/smartctl_exporter",
    "library/postgres": None,
    "library/redis": None,
    "library/alpine": None,
}

_GITHUB = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)")

# Wording that usually means the update needs something from you. STRONG
# lines are listed first; WEAK ones ("deprecated", "renamed") are worth a look.
STRONG = re.compile(
    r"(?i)(breaking|backwards?[- ]incompatib|not backwards?[- ]compatib|action required|"
    r"manual (intervention|steps?|migration)|migration (is )?required|"
    r"requires? (a )?(manual|database|db) (migration|step|change)|before (upgrading|updating)|"
    r"upgrade notes|must (now )?(be )?(set|update|change|migrat|re-?run|reconfigur))")
WEAK = re.compile(
    r"(?i)(deprecat|\brenam(e|ed|es|ing)\b|no longer (support|work|availab|accept|read|use)|"
    r"\bremov(e|ed|es|al) (of |the )?(support|option|setting|variable|env|flag|parameter|"
    r"endpoint|api|config|feature|legacy)|drop(ped|s|ping)? support|environment variable|"
    r"\benv var|minimum (supported |required )?version|end of life|\beol\b)")

log = logging.getLogger("notes")
_lock = threading.RLock()     # guards _notes, _cache and _blocked_until
_notes = {}                   # container name -> notes (see _build)
_cache = {}                   # GitHub URL -> {"etag", "fetched", "releases"}
_blocked_until = 0.0          # GitHub's rate-limit reset, epoch seconds
_run_lock = threading.Lock()  # one refresh at a time
_again = threading.Event()    # a refresh was asked for while one ran


class NotesError(Exception):
    """Notes couldn't be fetched; the message is short enough for the page."""


# --- Files ----------------------------------------------------------------


def load():
    """Read the notes and the release cache saved by the last run."""
    for path, target in ((NOTES_PATH, _notes), (CACHE_PATH, _cache)):
        try:
            with open(path) as file:
                saved = json.load(file)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            log.error("ignoring unreadable %s: %s", path, error)
            continue
        with _lock:
            target.update(saved)


def _save(path, data):
    # Same atomic write as store.save(): a crash never leaves half a file.
    with _lock:
        text = json.dumps(data, ensure_ascii=False)
    temporary = path + ".tmp"
    with open(temporary, "w") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


# --- What the page and the API get ------------------------------------------


def brief(name):
    """The few fields /api/status carries for one container, or None."""
    with _lock:
        notes = _notes.get(name)
        if not notes:
            return None
        return {key: notes.get(key) for key in
                ("state", "message", "repo", "url", "range", "how", "count", "flag_count",
                 "strong")} | {"flagged": notes.get("flagged", [])[:6]}


def full(name):
    """Everything for one container: GET /api/notes/<name>."""
    with _lock:
        notes = _notes.get(name)
        return json.loads(json.dumps(notes)) if notes else None


# --- Refreshing ---------------------------------------------------------------


def refresh_async():
    """Rebuild notes for the waiting updates, in the background.

    Asked for while a refresh is running, it runs once more afterwards, so
    the last check's results are always the ones described.
    """
    _again.set()
    if _run_lock.acquire(blocking=False):
        threading.Thread(target=_refresh_loop, name="notes", daemon=True).start()


def _refresh_loop():
    try:
        while _again.is_set():
            _again.clear()
            try:
                _refresh()
            except Exception as error:  # never let the thread die silently
                log.exception("release notes refresh failed: %s", error)
    finally:
        _run_lock.release()


def _refresh():
    with store.lock:
        pending = [json.loads(json.dumps(r)) for r in store.data["containers"].values()
                   if r["status"] in PENDING]
    changed = False
    for result in pending:
        name = result["name"]
        key = _key_of(result)
        with _lock:
            old = _notes.get(name)
        if old and old.get("key") == key and old.get("state") != "error":
            updated = versions.parse_time(old.get("updated"))
            if updated and _now() - updated < RECHECK:
                continue
        try:
            notes = _build(result)
        except NotesError as error:
            notes = {"state": "error", "message": str(error)}
        except Exception as error:  # a surprise in one container must not stop the rest
            log.exception("notes for %s failed", name)
            notes = {"state": "error", "message": f"unexpected error: {error}"}
        notes.update(key=key, updated=_now().isoformat(timespec="seconds"))
        notes.setdefault("repo", None)
        notes.setdefault("url", None)
        with _lock:
            _notes[name] = notes
        changed = True
        log.info("notes for %s: %s", name, _describe(notes))

    # Forget containers that no longer wait for an update.
    names = {r["name"] for r in pending}
    with _lock:
        for name in [n for n in _notes if n not in names]:
            del _notes[name]
            changed = True
    if changed:
        _save(NOTES_PATH, _notes)


def _describe(notes):
    if notes["state"] == "ok":
        return (f"{notes['count']} release(s) {notes['range']}"
                + (f", {notes['flag_count']} flagged line(s)" if notes["flag_count"] else ""))
    return f"{notes['state']}: {notes.get('message')}"


def _key_of(result):
    """What the notes depend on: the image and both versions."""
    running, available = result["running"] or {}, result.get("available") or {}
    return "|".join(str(part) for part in (
        result["ref"], running.get("full"), running.get("created"),
        available.get("full"), available.get("created")))


def _now():
    return datetime.now(timezone.utc)


# --- Building one container's notes -------------------------------------------


def _build(result):
    repo = repo_for(result)
    if not repo:
        return {"state": "none", "message": "no release notes on GitHub for this image"}
    base = {"repo": repo, "url": f"https://github.com/{repo}/releases"}

    running, available = result["running"] or {}, result.get("available") or {}
    if not available.get("full") and not available.get("created"):
        return base | {"state": "error", "message": "the new image's version is unknown"}

    releases, where, pages = [], {}, 0
    while pages < MAX_PAGES:
        pages += 1
        page = _releases(repo, pages, need=available)
        releases += page
        where = _place(releases, running, available)
        # Stop once the running version is found, or the list reaches back
        # past it; a short page is the end of the list.
        if where["lo_found"] or len(page) < PER_PAGE or _older_than(page, running):
            break

    if where["hi"] is None:
        return base | {"state": "error",
                       "message": "couldn't find the new version among the GitHub releases"}
    if where["lo"] is None and where["lo_date"] is None:
        return base | {"state": "error",
                       "message": "couldn't place the running version among the GitHub releases"}

    picked = [r for r in releases if _in_range(r, where)]
    if where["how"] == "by build date":  # name the releases the build dates were matched to
        lo_text = _tag_for(releases, where["lo"]) or running.get("display") or "?"
        hi_text = _tag_for(releases, where["hi"]) or available.get("display") or "?"
    else:
        lo_text, hi_text = running.get("display") or "?", available.get("display") or "?"
    picked.sort(key=lambda r: (_vkey(r["tag"]), r["date"] or ""), reverse=True)
    shown = picked[:MAX_RELEASES]
    flagged = _flag(shown)
    return base | {
        "state": "ok" if picked else "empty",
        "message": None if picked else
            "no new release between these builds (a rebuild of the same version)",
        "range": f"{lo_text} → {hi_text}",
        "how": where["how"],
        "count": len(picked),
        "releases": shown,
        "flagged": flagged[:MAX_FLAGGED],
        "flag_count": len(flagged),
        "strong": any(f["strong"] for f in flagged),
    }


def repo_for(result):
    """GitHub "owner/repo" for a result, or None."""
    repository = registry.parse_ref(result["ref"]).repository
    if repository in REPOS:
        return REPOS[repository]
    for version in (result.get("available") or {}, result["running"] or {}):
        match = _GITHUB.search(version.get("source") or "")
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return None


# --- Versions ---------------------------------------------------------------


def _norm(text):
    """ "v1.6.2-ls366" -> "1.6.2-ls366"; "0.26.0.0+e42a525d" -> "0.26.0.0". """
    text = (text or "").strip().lower()
    text = re.sub(r"^v(?=\d)", "", text)
    return text.split("+", 1)[0]


def _vkey(text):
    """Comparable key: the numbers in a tag, trailing zeros dropped.

    "v1.6.2-ls366" -> (1, 6, 2, 366), "2026.09.0" -> (2026, 9),
    "0.26.0.0" -> (0, 26). Tags within one repository share a shape, which is
    all the comparison needs.
    """
    numbers = [int(n) for n in re.findall(r"\d+", _norm(text))]
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def _place(releases, running, available):
    """Where the running and the new version sit among the releases.

    Returns {"lo": key or None, "lo_date": datetime or None, "lo_found": bool,
             "hi": key or None, "prerelease": bool, "how": "..."}.
    """
    lo, lo_found, how_lo = _locate(releases, running)
    hi, _, how_hi = _locate(releases, available)
    hi_release = next((r for r in releases if hi is not None and _vkey(r["tag"]) == hi), None)
    return {
        "lo": lo,
        "lo_found": lo_found,
        "lo_date": None if running.get("versioned") else versions.parse_time(running.get("created")),
        "hi": hi,
        "prerelease": bool(hi_release and hi_release["prerelease"]),
        "how": "by version" if how_lo == how_hi == "version" else "by build date",
    }


def _locate(releases, version):
    """(key, found among the releases, "version" or "date") for one image."""
    if version.get("versioned"):
        raw = version.get("full") or ""
        for release in releases:
            if _norm(release["tag"]) == _norm(raw):
                return _vkey(release["tag"]), True, "version"
        key = _vkey(raw)
        if len(key) >= 2:
            match = any(_vkey(r["tag"]) == key for r in releases)
            return key, match, "version"
    # No usable version: the image holds the newest release published before
    # it was built.
    built = versions.parse_time(version.get("created"))
    if not built:
        return None, False, "date"
    before = [r for r in releases if not r["prerelease"] and r["date"]
              and versions.parse_time(r["date"]) <= built + SLACK]
    if not before:
        return None, False, "date"
    return max(_vkey(r["tag"]) for r in before), True, "date"


def _tag_for(releases, key):
    return next((r["tag"] for r in releases if key is not None and _vkey(r["tag"]) == key), None)


def _in_range(release, where):
    if release["draft"] or (release["prerelease"] and not where["prerelease"]):
        return False
    key = _vkey(release["tag"])
    if not key or key > where["hi"]:
        return False
    if where["lo"] is not None:
        return key > where["lo"]
    # Nothing to compare with: everything published after the running build.
    published = versions.parse_time(release["date"])
    return bool(where["lo_date"] and published and published > where["lo_date"])


def _older_than(page, version):
    """Does this page already reach back before the running image was built?"""
    built = versions.parse_time(version.get("created"))
    dates = [versions.parse_time(r["date"]) for r in page if r["date"]]
    return bool(built and dates and min(dates) < built - timedelta(days=30))


# --- Flagging -----------------------------------------------------------------

_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\([^)]*\)")   # one level of [nested] brackets
_MENTION = re.compile(r"(?:\bby )?@[\w.\[\]-]+")
_URL = re.compile(r"https?://\S+")
_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")
_NOISE = re.compile(r"(?i)^(full changelog|ci report|from newest to oldest|"
                    r"linuxserver changes|remote changes|no changes)\b")


def _flag(releases):
    """Lines that match STRONG or WEAK, strong ones first, without repeats."""
    found, seen = [], set()
    for release in releases:
        for raw in (release["body"] or "").splitlines():
            line = _clean(raw)
            if len(line) < 12 or _NOISE.match(line) or line.lower() in seen:
                continue
            strong = bool(STRONG.search(line))
            if strong or WEAK.search(line):
                seen.add(line.lower())
                found.append({"tag": release["tag"], "line": line[:240], "strong": strong})
    found.sort(key=lambda f: not f["strong"])  # stable: release order kept within each kind
    return found


def _clean(line):
    line = _LINK.sub(r"\1", line)
    line = _URL.sub("", line)
    line = _MENTION.sub("", line)
    line = _HASH.sub("", line)
    line = re.sub(r"[*_`#>]+", "", line)
    line = re.sub(r"^\s*[-+•]\s*", "", line)
    line = re.sub(r"\(\s*\)|\s+", " ", line).strip(" -:·,")
    return re.sub(r"\s+(in|by)$", "", line)  # what's left of "by @someone in <link>"


# --- GitHub -------------------------------------------------------------------


def _releases(repo, page, need):
    """One page of a repository's releases, newest first, from cache or GitHub.

    A cached first page younger than FRESH is used as it is, unless the new
    version (`need`) isn't on it yet - then GitHub is asked again.
    """
    global _blocked_until
    url = f"{API}/repos/{repo}/releases?per_page={PER_PAGE}&page={page}"
    with _lock:
        cached = _cache.get(url)
    if cached:
        fetched = versions.parse_time(cached.get("fetched"))
        fresh = fetched and _now() - fetched < FRESH
        if fresh and (page > 1 or _listed(cached["releases"], need)):
            return cached["releases"]
    if time.time() < _blocked_until:
        if cached:
            return cached["releases"]
        until = datetime.fromtimestamp(_blocked_until, versions.LOCAL_TZ).strftime("%H:%M")
        raise NotesError(f"GitHub rate limit reached, notes again after {until}")

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "homelab-updater",
               "X-GitHub-Api-Version": "2022-11-28"}
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    status, response_headers, body = _http(url, headers)

    if status == 304 and cached:
        releases = cached["releases"]
        etag = cached.get("etag")
    elif status == 200:
        releases = [_trim(r) for r in json.loads(body)]
        etag = response_headers.get("ETag")
    elif status in (403, 429) and response_headers.get("X-RateLimit-Remaining") == "0":
        _blocked_until = float(response_headers.get("X-RateLimit-Reset") or time.time() + 3600)
        if cached:
            return cached["releases"]
        until = datetime.fromtimestamp(_blocked_until, versions.LOCAL_TZ).strftime("%H:%M")
        raise NotesError(f"GitHub rate limit reached, notes again after {until}")
    elif status == 404:
        raise NotesError(f"github.com/{repo} has no releases (or doesn't exist)")
    else:
        raise NotesError(f"GitHub answered HTTP {status}")

    with _lock:
        _cache[url] = {"etag": etag, "fetched": _now().isoformat(timespec="seconds"),
                       "releases": releases}
    _save(CACHE_PATH, _cache)
    return releases


def _listed(releases, version):
    """Is this version among the releases (or not a version at all)?"""
    if not version.get("versioned"):
        return True
    key = _vkey(version.get("full"))
    return any(_norm(r["tag"]) == _norm(version.get("full")) or _vkey(r["tag"]) == key
               for r in releases)


def _trim(release):
    return {
        "tag": release.get("tag_name") or "",
        "name": (release.get("name") or "").strip(),
        "date": release.get("published_at") or release.get("created_at"),
        "prerelease": bool(release.get("prerelease")),
        "draft": bool(release.get("draft")),
        "url": release.get("html_url"),
        "body": (release.get("body") or "")[:MAX_BODY],
    }


def _http(url, headers):
    """GET with one retry for network errors; (status, headers, body)."""
    for attempt in (1, 2):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:  # 304 lands here too
            return error.code, error.headers, error.read()
        except OSError as error:  # DNS failure, refused, reset, timeout
            if attempt == 2:
                raise NotesError(f"cannot reach GitHub: {getattr(error, 'reason', None) or error}") from None
            time.sleep(2)
    raise AssertionError("unreachable")
