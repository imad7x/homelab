"""Entry point: the web page, a small JSON API, and the background threads.

  GET  /                the page (index.html)
  GET  /api/status      everything the page shows
  GET  /api/summary     counts for the Homepage widget
  GET  /api/jobs/<id>   one update job, with its log
  GET  /api/notes/<name> release notes between a container's running and new version
  POST /api/check       start a check now
  POST /api/update      {"containers": [...]} or {"all": true}, optional "dry_run": true

Every POST must carry the header "X-Updater: 1" and a JSON body. A browser
only lets another web page send a custom header after a CORS preflight,
and this server never approves one (it sends no CORS headers at all), so a
malicious page cannot make a visitor's browser start an update.

POSTs must also be addressed to an IP, localhost, or a name in ALLOWED_HOSTS.
That closes DNS rebinding, where a malicious site re-points its own name at
192.168.0.10 so the browser treats this server as "same origin" and lets it
send the header; the Host header then still carries the attacker's name.
"""

import ipaddress
import json
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import checks
import jobs
import notes
import store

PORT = int(os.environ.get("PORT", "8084"))
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_BODY = 64 * 1024
# Names (besides IPs and localhost) the page may be opened under and still
# start checks and updates. Comma-separated.
ALLOWED_HOSTS = {name.strip().lower() for name in
                 os.environ.get("ALLOWED_HOSTS", "updates.[[private:DOMAIN]]").split(",") if name.strip()}

log = logging.getLogger("http")

# Status groups in the order the page lists them.
_GROUP = {"update": 0, "restart": 0, "error": 1, "ok": 2, "local": 3, "nocompose": 3}


# --- Payloads ---------------------------------------------------------------


def status_payload():
    with store.lock:
        results = sorted(store.data["containers"].values(),
                         key=lambda r: (_GROUP[r["status"]], r["name"]))
        all_jobs = store.data["jobs"]
        active = {j["container"]: j for j in all_jobs if j["status"] in ("queued", "running")}
        current = next((j for j in all_jobs if j["status"] == "running"), None)
        payload = {
            "now": checks.now().isoformat(timespec="seconds"),
            "tz": str(checks.LOCAL_TZ),
            "schedule": checks.schedule_text(),
            "last_check": store.data["last_check"],
            "next_check": checks.next_check().isoformat(timespec="seconds"),
            "checking": checks.checking,
            "check_error": store.data["check_error"],
            "containers": [dict(r, job=_job_brief(active.get(r["name"])),
                                notes=notes.brief(r["name"]) if r["status"] in notes.PENDING else None)
                           for r in results],
            "counts": _counts(results),
            "current": dict(current, log=list(current["log"])) if current else None,
            "queue": [_job_brief(j) for j in all_jobs if j["status"] == "queued"],
            "jobs": [_job_brief(j) for j in reversed(all_jobs)
                     if j["status"] not in ("queued", "running")],
        }
    return payload


def summary_payload():
    with store.lock:
        counts = _counts(store.data["containers"].values())
        last_check = store.data["last_check"]
        check_error = store.data["check_error"]
    return {
        "available": counts["update"] + counts["restart"],
        # Containers whose registry answered; lookups that failed are "errors".
        "checked": counts["update"] + counts["restart"] + counts["ok"],
        "errors": counts["error"],
        "last_check": last_check,
        "next_check": checks.next_check().isoformat(timespec="seconds"),
        "checking": checks.checking,
        "check_error": check_error["message"] if check_error else None,
        "updating": jobs.busy(),
    }


def _counts(results):
    counts = dict.fromkeys(_GROUP, 0)
    for result in results:
        counts[result["status"]] += 1
    return counts


def _job_brief(job):
    if not job:
        return None
    return {key: job[key] for key in
            ("id", "container", "stack", "status", "message", "from", "to",
             "created", "started", "finished")}


# --- HTTP -------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "updater"
    sys_version = ""

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._page()
        if path == "/api/status":
            return self._json(200, status_payload())
        if path == "/api/summary":
            return self._json(200, summary_payload())
        match = re.fullmatch(r"/api/notes/([A-Za-z0-9_.-]{1,128})", path)
        if match:
            found = notes.full(match.group(1))
            if not found:
                return self._json(404, {"error": "no release notes for that container"})
            return self._json(200, found)
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{1,32})", path)
        if match:
            job = store.find_job(match.group(1))
            if not job:
                return self._json(404, {"error": "no such job"})
            with store.lock:
                return self._json(200, dict(job, log=list(job["log"])))
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._post_allowed():
            return self._json(403, {"error": "POST needs the X-Updater: 1 header and a JSON body"})
        if not self._host_allowed():
            return self._json(403, {"error": "open the page by IP, localhost or a name in ALLOWED_HOSTS"})
        try:
            body = self._read_json()
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        path = self.path.split("?", 1)[0]
        if path == "/api/check":
            started = checks.start_check()
            return self._json(202, {"started": started, "checking": True})
        if path == "/api/update":
            return self._update(body)
        return self._json(404, {"error": "not found"})

    # --- POST /api/update ---

    def _update(self, body):
        dry_run = body.get("dry_run") is True
        if body.get("all") is True:
            planned = jobs.plan()
        else:
            names = body.get("containers")
            if not (isinstance(names, list) and names and all(isinstance(n, str) for n in names)):
                return self._json(400, {"error": 'send {"containers": ["name", ...]} or {"all": true}'})
            problems = jobs.problems(names)
            if problems:
                return self._json(400, {"error": "; ".join(problems)})
            planned = jobs.plan(names)

        plan = [jobs.brief(result) for result in planned]
        if dry_run:
            return self._json(200, {"dry_run": True, "plan": plan})
        created, skipped = jobs.enqueue(planned)
        return self._json(202, {"jobs": [job["id"] for job in created], "plan": plan,
                                "skipped": skipped})

    # --- helpers ---

    def _post_allowed(self):
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return self.headers.get("X-Updater") == "1" and content_type == "application/json"

    def _host_allowed(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):  # [IPv6]:port
            host = host[1:host.find("]")] if "]" in host else host
        else:
            host = host.rsplit(":", 1)[0]
        if host == "localhost" or host in ALLOWED_HOSTS:
            return True
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise ValueError("request body is not valid JSON") from None
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def _page(self):
        with open(PAGE, "rb") as file:
            content = file.read()
        self._send(200, content, "text/html; charset=utf-8", {
            # Inline script and style only, no framing (click-jacking the
            # Update button from another site), nothing loaded from elsewhere.
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                       "style-src 'unsafe-inline'; connect-src 'self'; "
                                       "img-src 'self' data:; frame-ancestors 'none'; "
                                       "base-uri 'none'; form-action 'none'",
        })

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _send(self, status, content, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def log_request(self, code="-", size="-"):
        # The page polls /api/status every few seconds; only log what matters.
        if self.command != "GET" or not str(code).startswith("2"):
            log.info("%s %s %s -> %s", self.client_address[0], self.command, self.path, code)

    def log_error(self, format, *args):
        log.warning("%s " + format, self.client_address[0], *args)


# --- Start-up ---------------------------------------------------------------


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    # The docker CLI keeps its config (and would keep credentials) here. The
    # host's ~/.docker is deliberately not mounted, so pulls stay anonymous.
    os.makedirs(os.environ.get("DOCKER_CONFIG", "/state/docker-config"), exist_ok=True)
    store.load()
    notes.load()
    log.info("updater starting on port %d; checks %s", PORT, checks.schedule_text())

    threading.Thread(target=checks.scheduler, name="scheduler", daemon=True).start()
    threading.Thread(target=jobs.worker, name="worker", daemon=True).start()
    notes.refresh_async()  # notes for updates found before a restart

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
