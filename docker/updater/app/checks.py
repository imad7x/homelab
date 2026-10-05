"""Update checks: does each running container have a newer image waiting?

For every running container the check settles on one status:

  update     the registry's tag points to an image we don't have yet
  restart    that image is already downloaded, the container just still runs
             the old one (someone pulled without recreating)
  ok         the container runs what the registry's tag points to
  error      the registry lookup failed (never counted as an update)
  local      built on this machine or pinned to a digest: nothing to check
  nocompose  not started by compose, or its compose file is gone, so there
             is no safe way to recreate it

Checks run on a schedule (CHECK_TIME, optionally weekly on CHECK_DAY), on
demand, and for the affected containers after every update job.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import dockerops
import registry
import store
import versions
from versions import LOCAL_TZ

WORKERS = 6
SELF_PROJECT = "updater"  # this app's own stack; it never updates itself

log = logging.getLogger("check")
_lock = threading.Lock()  # one check at a time
checking = False          # shown on the page while a check runs


def now():
    return datetime.now(LOCAL_TZ)


# --- Running a check --------------------------------------------------------


def start_check(names=None):
    """Start a check in the background. False if one is already running."""
    if not _lock.acquire(blocking=False):
        return False
    threading.Thread(target=_locked_check, args=(names,), daemon=True).start()
    return True


def run_check(names=None):
    """Check now, in this thread, waiting for a running check to end first."""
    _lock.acquire()
    _locked_check(names)


def _locked_check(names):
    global checking
    checking = True
    try:
        _check(names)
    except Exception as error:  # a check must never kill its thread silently
        log.exception("check failed: %s", error)
        _record_failure(error)
    finally:
        checking = False
        _lock.release()


def _check(names):
    started = time.monotonic()
    scope = "all containers" if names is None else ", ".join(sorted(names))
    log.info("check started (%s)", scope)

    everything = dockerops.running_containers()
    containers = [c for c in everything if names is None or c["name"] in names]
    arch = dockerops.host_arch()

    results = {}
    to_query = []
    for container in containers:
        skip = _not_checkable(container)
        if skip:
            results[container["name"]] = _result(container, *skip)
        else:
            to_query.append(container)

    results.update(_compare_with_registries(to_query, arch))
    _mark_drift(to_query, results)
    _save(results, full=names is None, running={c["name"] for c in everything})

    counts = {}
    for result in results.values():
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items())) or "nothing running"
    log.info("check finished in %.1fs: %s", time.monotonic() - started, summary)


def _record_failure(error):
    """Keep a failed check's reason for the page; the last good results stay."""
    message = (str(error).strip() or type(error).__name__)[:300]
    with store.lock:
        store.data["check_error"] = {"at": now().isoformat(timespec="seconds"), "message": message}
    try:
        store.save()
    except OSError as save_error:
        log.error("could not save the failed check: %s", save_error)


def _not_checkable(container):
    """(status, reason) for containers that are never looked up, else None."""
    if container["project"] == SELF_PROJECT:
        return "local", "built locally"
    if "@sha256:" in container["ref"]:
        return "local", "pinned to a digest"
    if not container["image_repo_digests"]:  # never pulled from a registry
        return "local", "built locally"
    if not container["project"] or not container["config_files"]:
        return "nocompose", "not started by docker compose"
    missing = dockerops.missing_compose_files(container)
    if missing:
        return "nocompose", f"compose file {missing[0]} is missing"
    return None


def _compare_with_registries(containers, arch):
    """Look each distinct image up once, then decide every container's status."""
    refs = {}
    for container in containers:
        ref = registry.parse_ref(container["ref"])
        refs[ref.key] = ref
    remote = _parallel(registry.remote_digest, refs)
    local = dockerops.local_images(c["ref"] for c in containers)

    results = {}
    wanted_configs = {}
    for container in containers:
        ref = registry.parse_ref(container["ref"])
        digest = remote[ref.key]
        if isinstance(digest, Exception):
            results[container["name"]] = _result(container, "error", str(digest))
            continue
        tag_image = local.get(container["ref"])
        if not tag_image or not _has_digest(tag_image, digest):
            status, available = "update", None  # filled in from the registry below
            wanted_configs[ref.key] = ref
        elif container["image_id"] != tag_image["Id"]:
            status = "restart"
            available = versions.version_info((tag_image.get("Config") or {}).get("Labels"),
                                              tag_image.get("Created"))
        else:
            status, available = "ok", None
        results[container["name"]] = _result(container, status, "", available, digest)

    # Only images that moved cost a manifest GET, and only once per image.
    configs = _parallel(lambda ref: registry.image_config(ref, remote[ref.key], arch), wanted_configs)
    for container in containers:
        result = results[container["name"]]
        if result["status"] == "update":
            config = configs[registry.parse_ref(container["ref"]).key]
            if isinstance(config, Exception):
                result["available"] = {"display": "newer image", "full": str(config), "date": None}
            else:
                result["available"] = versions.version_info(config["labels"], config["created"])
            _describe(result)
    return results


def _has_digest(image, digest):
    """Is `digest` one of the registry digests this local image was pulled as?"""
    return any(entry.endswith("@" + digest) for entry in image.get("RepoDigests") or [])


def _parallel(function, refs):
    """Run function(ref) for each ref on a few threads; key -> value or exception."""
    if not refs:
        return {}
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {key: pool.submit(function, ref) for key, ref in refs.items()}
    outcomes = {}
    for key, future in futures.items():
        try:
            outcomes[key] = future.result()
        except registry.RegistryError as error:
            outcomes[key] = error
        except Exception as error:  # unexpected: keep the check going, show it
            log.exception("lookup of %s failed", key)
            outcomes[key] = RuntimeError(f"unexpected error: {error}")
    return outcomes


def _mark_drift(containers, results):
    """Note containers whose compose file changed since they were created.

    `docker compose config --hash` runs once per project (compose file set),
    and each service's hash is compared with the one compose stamped on its
    container when it was created.
    """
    projects = {}
    for container in containers:
        key = (container["project"], container["working_dir"],
               tuple(container["config_files"]), tuple(container["env_files"]))
        projects.setdefault(key, []).append(container)
    for group in projects.values():
        try:
            hashes = dockerops.config_hashes(group[0])
        except dockerops.DockerError as error:
            log.warning("compose config for %s failed: %s", group[0]["project"], error)
            continue
        for container in group:
            now_hash = hashes.get(container["service"])
            results[container["name"]]["drift"] = bool(now_hash and now_hash != container["config_hash"])


def _result(container, status, reason="", available=None, remote_digest=None):
    ref = registry.parse_ref(container["ref"])
    result = {
        "name": container["name"],
        "stack": container["project"],
        "service": container["service"],
        "ref": container["ref"],
        "tag": None if ref.digest else ref.tag,
        "status": status,
        "reason": reason,
        "running": versions.version_info(container["image_labels"], container["image_created"]),
        "available": available,
        "remote_digest": remote_digest,
        "image_id": container["image_id"],
        "drift": False,
        "checked_at": now().isoformat(timespec="seconds"),
    }
    return _describe(result)


def _describe(result):
    """Add the wording the page shows, so it lives in one place."""
    status, reason = result["status"], result["reason"]
    result["status_text"] = {
        "update": "Update available",
        "restart": "Downloaded — restart to apply",
        "ok": "Up to date",
        "local": "Built locally" if reason == "built locally" else reason.capitalize(),
        "nocompose": f"Can't update: {reason}",
        "error": f"Check failed: {reason}",
    }[status]
    if status in ("update", "restart"):
        result["available_text"] = versions.available_text(result["running"], result["available"])
    elif status == "ok":
        result["available_text"] = "Up to date"
    else:
        result["available_text"] = None
    return result


def _save(results, full, running):
    with store.lock:
        store.data["check_error"] = None
        if full:
            store.data["containers"] = results
            store.data["last_check"] = now().isoformat(timespec="seconds")
        else:
            # A partial check refreshes only its containers; the rest keep
            # their last result unless they are no longer running at all.
            kept = {name: result for name, result in store.data["containers"].items()
                    if name in running and name not in results}
            store.data["containers"] = {**kept, **results}
    store.save()


# --- Schedule ---------------------------------------------------------------

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _schedule():
    """(hour, minute, weekday or None) from CHECK_TIME and CHECK_DAY."""
    text = os.environ.get("CHECK_TIME", "06:00").strip()
    try:
        hour, minute = (int(part) for part in text.split(":"))
        datetime(2000, 1, 1, hour, minute)  # validates the ranges
    except ValueError:
        log.error("CHECK_TIME=%r is not HH:MM; using 06:00", text)
        hour, minute = 6, 0
    day_text = os.environ.get("CHECK_DAY", "").strip().lower()[:3]
    if day_text and day_text not in DAYS:
        log.error("CHECK_DAY=%r is not mon..sun; checking daily", day_text)
        day_text = ""
    return hour, minute, (DAYS.index(day_text) if day_text else None)


HOUR, MINUTE, WEEKDAY = _schedule()
INTERVAL = timedelta(days=1 if WEEKDAY is None else 7)


def schedule_text():
    when = f"{HOUR:02d}:{MINUTE:02d}"
    if WEEKDAY is None:
        return f"daily at {when}"
    return f"every {DAYS[WEEKDAY].capitalize()} at {when}"


def next_check(after=None):
    after = after or now()
    at = after.replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
    if WEEKDAY is not None:
        at += timedelta(days=(WEEKDAY - at.weekday()) % 7)
    while at <= after:
        at += INTERVAL
    return at


def scheduler():
    """Thread: run the scheduled checks forever."""
    last = versions.parse_time(store.data.get("last_check"))
    if last is None or now() - last > INTERVAL:
        # Never checked, or the box was off through a scheduled check: catch
        # up shortly after start instead of waiting for the next slot.
        log.info("catch-up check in 20s (last check: %s)", last or "never")
        time.sleep(20)
        run_check()
    while True:
        due = next_check()
        log.info("next check %s", due.strftime("%a %Y-%m-%d %H:%M"))
        # Sleep in short steps so a clock change (NTP after boot) can't make
        # us oversleep the slot by hours.
        while now() < due:
            time.sleep(min(60.0, max(1.0, (due - now()).total_seconds())))
        run_check()
