"""Update jobs: pull, recreate, prove it works, or put the old image back.

One job updates one container:

  1. docker compose pull <service>            (fetch the new image)
  2. docker compose up -d --no-deps <service> (recreate on it)
  3. wait until it is healthy / steadily running
  4. success: remove the old image
     failure: tag the old image back and recreate again ("rolled back")

Jobs run strictly one at a time on a single worker thread. Asking for
several containers at once (or "update all") pulls every image first and
only then recreates them one by one, with the containers that carry DNS,
the reverse proxy and the tunnel last: recreating Pi-hole briefly takes
the daemon's DNS away, which would break any pull still to come.
"""

import logging
import queue
import threading
import uuid

import checks
import dockerops
import store

log = logging.getLogger("update")

# Statuses the API accepts for an update. "ok" and "error" are allowed on
# purpose: pulling and recreating is harmless when nothing changed. Built
# locally / no compose file are refused, since there is nothing safe to run.
UPDATABLE = {"update", "restart", "ok", "error"}
AVAILABLE = {"update", "restart"}  # what "update all" picks

# Recreated last, in this order: DNS (Pi-hole), the reverse proxy this page
# may be served through (Caddy), Pi-hole's DNS-over-HTTPS upstream, and the
# tunnel for remote access.
LAST = ["pihole", "caddy", "cloudflared", "cloudflared_tunnel"]

MAX_LOG_LINES = 1500

_batches = queue.Queue()


# --- Planning and queueing (called from the HTTP handlers) -------------------


def problems(names):
    """Why each of these names cannot be updated (empty list: all fine)."""
    with store.lock:
        results = store.data["containers"]
        found = []
        for name in names:
            result = results.get(name)
            if result is None:
                found.append(f"{name}: no such running container (as of the last check)")
            elif result["status"] not in UPDATABLE or result["stack"] == checks.SELF_PROJECT:
                found.append(f"{name}: {result['status_text']}")
    return found


def plan(names=None):
    """Containers an update would touch, in the order it would touch them.

    names=None means "update all": everything with an update waiting.
    """
    with store.lock:
        results = store.data["containers"]
        if names is None:
            chosen = [r for r in results.values() if r["status"] in AVAILABLE]
        else:
            chosen = [results[name] for name in dict.fromkeys(names)]
    return sorted(chosen, key=_order)


def _order(result):
    name = result["name"]
    return (LAST.index(name) + 1 if name in LAST else 0, result["stack"], name)


def brief(result):
    """What the confirmation dialog and API show for a planned container."""
    return {
        "name": result["name"],
        "stack": result["stack"],
        "status": result["status"],
        "running": result["running"]["display"],
        "available": result.get("available_text"),
    }


def enqueue(planned):
    """Queue jobs for the planned containers.

    Containers that already have a job waiting or running are skipped.
    Returns (new jobs, names skipped).
    """
    with store.lock:
        busy = {j["container"] for j in store.data["jobs"] if j["status"] in ("queued", "running")}
        batch = [_new_job(result) for result in planned if result["name"] not in busy]
        skipped = [result["name"] for result in planned if result["name"] in busy]
        for job in batch:
            store.add_job(job)
    if batch:
        store.save()
        _batches.put(batch)
        log.info("queued: %s", ", ".join(j["container"] for j in batch))
    return batch, skipped


def _new_job(result):
    return {
        "id": uuid.uuid4().hex[:10],
        "container": result["name"],
        "stack": result["stack"],
        "from": result["running"]["display"],
        # An "ok" container keeps its version; only a real update has a target.
        "to": (result.get("available_text") if result["status"] in AVAILABLE else None)
              or result["running"]["display"],
        "status": "queued",   # queued -> running -> updated | rolled back | failed
        "message": "",
        "created": checks.now().isoformat(timespec="seconds"),
        "started": None,
        "finished": None,
        "log": [],
    }


def busy():
    with store.lock:
        return any(j["status"] in ("queued", "running") for j in store.data["jobs"])


# --- The worker ---------------------------------------------------------------


def worker():
    """Thread: run queued batches one after another, forever."""
    while True:
        batch = _batches.get()
        try:
            _run_batch(batch)
        except Exception as error:
            log.exception("update batch crashed: %s", error)
            for job in batch:
                if job["status"] in ("queued", "running"):
                    _finish(job, "failed", f"internal error: {error}")
        # Refresh what the page shows for the containers just touched.
        checks.run_check({job["container"] for job in batch})


def _run_batch(batch):
    targets = {}
    for job in batch:
        target = _target(job)
        if target:
            targets[job["id"]] = target

    pulled = False
    if len(targets) > 1:
        log.info("pulling %d images before recreating anything", len(targets))
        for job in batch:
            if job["id"] in targets:
                _set(job, status="running", message="downloading", started=_now())
                if not _pull(job, targets[job["id"]]):
                    del targets[job["id"]]
                else:
                    _set(job, status="queued", message="downloaded, waiting its turn")
        pulled = True

    for job in batch:
        if job["id"] in targets:
            _update(job, targets[job["id"]], pulled)


def _target(job):
    """Re-read the container right before touching it; None (and a failed
    job) if it can no longer be updated safely."""
    name = job["container"]
    target = dockerops.container(name)
    if target is None or not target["running"]:
        return _finish(job, "failed", "container is not running any more")
    if target["project"] == checks.SELF_PROJECT or not target["image_repo_digests"]:
        return _finish(job, "failed", "built locally; not updated here")
    if not target["project"] or not target["config_files"]:
        return _finish(job, "failed", "not started by docker compose")
    missing = dockerops.missing_compose_files(target)
    if missing:
        return _finish(job, "failed", f"compose file {missing[0]} is missing")
    return target


def _pull(job, target):
    say = _logger(job)
    say(f"Pulling the image for {target['name']} ({target['ref']})", step=True)
    code = dockerops.run_logged(dockerops.compose_command(target) + ["pull", target["service"]], say)
    if code != 0:
        _finish(job, "failed", "pull failed; nothing was changed")
        return False
    return True


def _update(job, target, pulled):
    name, service = target["name"], target["service"]
    old_image, old_container = target["image_id"], target["id"]
    say = _logger(job)
    _set(job, status="running", message="updating", started=job["started"] or _now())
    say(f"Updating {name} (stack {target['project']}, service {service}); "
        f"running image {_short(old_image)}", step=True)

    if not pulled and not _pull(job, target):
        return
    # What the tag points to after the pull: the image a rollback discards.
    new_image = dockerops.image_id(target["ref"])

    say(f"Recreating {name}", step=True)
    compose = dockerops.compose_command(target)
    if dockerops.run_logged(compose + ["up", "-d", "--no-deps", service], say) != 0:
        return _roll_back(job, target, new_image, "docker compose up failed")

    now = dockerops.container(name)
    if now is None:
        return _roll_back(job, target, new_image, "container is gone after docker compose up")
    if now["id"] == old_container:
        # Compose saw nothing to change: same image, same config.
        say("Compose left the container as it was: already on the newest image", step=True)
        return _finish(job, "updated", "already up to date; nothing changed")

    say(f"Started on image {_short(now['image_id'])}; waiting for it to be healthy", step=True)
    healthy, why = dockerops.wait_healthy(name, say)
    if not healthy:
        return _roll_back(job, target, now["image_id"], why)

    say(f"Healthy ({why})", step=True)
    if now["image_id"] != old_image:
        _remove_image(old_image, say, "old")
    _finish(job, "updated", f"now on {job['to']}")


def _roll_back(job, target, new_image, reason):
    """Put the old image back under the same tag and recreate on it."""
    name, service, old_image = target["name"], target["service"], target["image_id"]
    say = _logger(job)
    say(f"FAILED: {reason}. Rolling back to image {_short(old_image)}", step=True)

    dockerops.run_logged(["docker", "tag", old_image, target["ref"]], say)
    # --pull never: a service with pull_policy: always would otherwise
    # download the new image again and undo the rollback.
    compose = dockerops.compose_command(target)
    code = dockerops.run_logged(compose + ["up", "-d", "--no-deps", "--pull", "never", service], say)
    healthy, why = (False, "docker compose up failed") if code else dockerops.wait_healthy(name, say)

    if new_image and new_image != old_image:
        _remove_image(new_image, say, "failed new")

    if healthy:
        say("Rolled back; the container runs the previous image again", step=True)
        return _finish(job, "rolled back", f"{reason}; back on the previous image")
    say(f"Rollback did not come up healthy either: {why}", step=True)
    return _finish(job, "failed", f"{reason}; rollback also failed ({why}), check it by hand")


def _remove_image(image, say, which):
    """Delete an image that nothing refers to any more.

    Only untagged images are removed. `docker image rm <id>` on an image with
    a single tag quietly deletes that tag too, and in a batch that tag can be
    the image the next job has just pulled (two services on the same image).
    Never forced either: if a container still uses it, docker refuses and
    we move on.
    """
    tags = dockerops.image_tags(image)
    if tags is None:
        return  # already gone
    if tags:
        say(f"Kept the {which} image {_short(image)}: still tagged {', '.join(tags)}")
        return
    code, output = dockerops.run(["docker", "image", "rm", image])
    if code == 0:
        say(f"Removed the {which} image {_short(image)}")
    else:
        reason = output.strip().splitlines()[-1] if output.strip() else "in use"
        say(f"Kept the {which} image {_short(image)}: {reason}")


# --- Job bookkeeping ----------------------------------------------------------


def _logger(job):
    """A function that appends timestamped lines to the job's log.

    step=True lines are the milestones; they also go to `docker logs updater`.
    """
    def say(text, step=False):
        stamp = checks.now().strftime("%H:%M:%S")
        with store.lock:
            lines = job["log"]
            lines.append(f"{stamp} {text}")
            if len(lines) > MAX_LOG_LINES:
                # Keep the start (what ran) and the newest output.
                if not lines[100].startswith("…"):
                    lines.insert(100, "… older output trimmed …")
                del lines[101:len(lines) - MAX_LOG_LINES + 101]
        if step:
            log.info("[%s] %s", job["container"], text)
            store.save()
    return say


def _set(job, **changes):
    with store.lock:
        job.update(changes)
    store.save()


def _finish(job, status, message):
    _set(job, status=status, message=message, finished=_now(), started=job["started"] or _now())
    log.info("[%s] %s: %s", job["container"], status, message)
    return None


def _now():
    return checks.now().isoformat(timespec="seconds")


def _short(image_id):
    return (image_id or "?").removeprefix("sha256:")[:12]
