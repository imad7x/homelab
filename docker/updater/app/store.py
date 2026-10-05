"""The app's memory: last check, per-container results and the job history.

Everything lives in one dict guarded by one lock, and is written to
/state/state.json after each change so it survives a restart. Writes go to
a temporary file first and are swapped in with os.replace, which is atomic:
a crash mid-write leaves the previous file intact, never half a file.
"""

import json
import logging
import os
import threading

PATH = os.environ.get("STATE_FILE", "/state/state.json")
MAX_JOBS = 30

log = logging.getLogger("store")
lock = threading.RLock()
data = {
    "last_check": None,   # ISO time of the last full check
    "check_error": None,  # {"at", "message"} while the last check failed
    "containers": {},     # name -> result of the last check
    "jobs": [],           # oldest first, at most MAX_JOBS
}


def load():
    try:
        with open(PATH) as file:
            saved = json.load(file)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as error:
        log.error("ignoring unreadable %s: %s", PATH, error)
        return
    with lock:
        data.update({key: saved.get(key, value) for key, value in data.items()})
        # A job that was queued or running when the app stopped will never
        # finish; say so instead of showing it as stuck forever.
        for job in data["jobs"]:
            if job["status"] in ("queued", "running"):
                job["status"] = "failed"
                job["message"] = "interrupted: the updater restarted"


def save():
    with lock:
        text = json.dumps(data, indent=1, ensure_ascii=False)
    temporary = PATH + ".tmp"
    with open(temporary, "w") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, PATH)


def add_job(job):
    with lock:
        data["jobs"].append(job)
        # Drop the oldest finished jobs beyond the limit; never a live one.
        while len(data["jobs"]) > MAX_JOBS:
            finished = [j for j in data["jobs"] if j["status"] not in ("queued", "running")]
            if not finished:
                break
            data["jobs"].remove(finished[0])


def find_job(job_id):
    with lock:
        return next((job for job in data["jobs"] if job["id"] == job_id), None)
