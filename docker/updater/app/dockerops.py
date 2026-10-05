"""Everything that talks to the Docker daemon, through the docker CLI.

The CLI is used instead of raw API calls because `docker compose` has to be
the CLI anyway, and because every command in a job log then reads exactly
as it would if typed on the host. Arguments are always passed as a list,
never through a shell.
"""

import json
import os
import re
import subprocess
import threading
import time

# A pull of a large image over a slow line can take a while; anything past
# this is treated as hung.
LONG_TIMEOUT = 1800

# Compose prints a progress bar line per layer every few hundred ms while
# pulling. "Pulling fs layer" / "Download complete" / "Pull complete" already
# show the progress, so the bars are left out of job logs.
PROGRESS_BAR = re.compile(r"^\s*\S+ (Downloading|Extracting|Waiting)\b")


class DockerError(Exception):
    pass


def run(args, timeout=60):
    """Run a command, return (exit code, combined stdout+stderr)."""
    try:
        done = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return done.returncode, done.stdout


def run_logged(args, log, timeout=LONG_TIMEOUT):
    """Run a command, sending each output line to `log` as it arrives.

    Streaming (rather than collecting) is what lets the page show a pull's
    progress live. A timer kills the command if it hangs. Returns the exit code.
    """
    log("$ " + " ".join(args))
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    watchdog = threading.Timer(timeout, process.kill)
    watchdog.start()
    try:
        for line in process.stdout:
            if line.strip() and not PROGRESS_BAR.match(line):
                log("  " + line.rstrip())
        code = process.wait()
    finally:
        watchdog.cancel()
    if code == -9:
        log(f"  (killed: still running after {timeout}s)")
    return code


def _inspect(kind, names):
    """`docker <kind> inspect` for many objects in one call.

    Returns the parsed list, in argument order, for the objects that exist.
    Missing ones are simply absent (docker exits 1 but still prints the rest).
    """
    if not names:
        return []
    try:
        done = subprocess.run(["docker", kind, "inspect", *names], capture_output=True,
                              text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise DockerError(f"docker {kind} inspect timed out") from None
    try:
        return json.loads(done.stdout or "[]")
    except ValueError:
        raise DockerError(done.stderr.strip()[:300] or f"docker {kind} inspect failed") from None


def host_arch():
    """The daemon's CPU architecture in registry terms (amd64, arm64, ...)."""
    code, output = run(["docker", "info", "--format", "{{.Architecture}}"])
    if code != 0:
        raise DockerError("cannot reach the Docker daemon: " + output.strip()[:200])
    arch = output.strip()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}.get(arch, arch)


# --- Discovery --------------------------------------------------------------


def running_containers():
    """Every running container, with the facts the update check needs.

    Stopped containers are left out on purpose: `up -d` would start them,
    which is not what "update" should mean.
    """
    code, output = run(["docker", "ps", "-q", "--no-trunc"])
    if code != 0:
        raise DockerError("docker ps failed: " + output.strip()[:200])
    containers = _inspect("container", output.split())
    images = {image["Id"]: image for image in
              _inspect("image", sorted({c["Image"] for c in containers}))}
    return [_describe(c, images.get(c["Image"], {})) for c in containers]


def _describe(container, image):
    labels = container["Config"].get("Labels") or {}

    def label(key):
        return labels.get("com.docker.compose." + key, "")

    return {
        "name": container["Name"].lstrip("/"),
        "id": container["Id"],
        "ref": container["Config"]["Image"],
        "image_id": container["Image"],
        "image_repo_digests": image.get("RepoDigests") or [],
        "image_labels": (image.get("Config") or {}).get("Labels") or {},
        "image_created": image.get("Created"),
        "project": label("project"),
        "service": label("service"),
        "working_dir": label("project.working_dir"),
        "config_files": _split(label("project.config_files")),
        "env_files": _split(label("project.environment_file")),
        "config_hash": label("config-hash"),
    }


def _split(value):
    return [part for part in value.split(",") if part]


def container(name):
    """Fresh facts about one container (running or not), or None if gone."""
    found = _inspect("container", [name])
    if not found:
        return None
    image = _inspect("image", [found[0]["Image"]])
    facts = _describe(found[0], image[0] if image else {})
    facts["running"] = bool(found[0]["State"].get("Running"))
    return facts


def local_images(refs):
    """Map each ref to the local image it points to right now (or None).

    Batched into one inspect. Docker leaves missing images out of the answer,
    so when anything is missing the refs are asked one by one to line up.
    """
    refs = sorted(set(refs))
    found = _inspect("image", refs)
    if len(found) == len(refs):
        return dict(zip(refs, found))
    return {ref: (_inspect("image", [ref]) or [None])[0] for ref in refs}


def image_id(ref):
    image = _inspect("image", [ref])
    return image[0]["Id"] if image else None


def image_tags(image):
    """The tags an image still carries ([] if none), or None if it is gone."""
    found = _inspect("image", [image])
    return (found[0].get("RepoTags") or []) if found else None


# --- Compose ----------------------------------------------------------------


def compose_command(target):
    """The `docker compose` prefix that addresses a container's own project.

    Built from the labels compose stamped on the container, so it names the
    same project, directory, files and env files the stack was started with.
    """
    args = ["docker", "compose", "--ansi", "never", "--progress", "plain",
            "-p", target["project"], "--project-directory", target["working_dir"]]
    for path in target["config_files"]:
        args += ["-f", path]
    for path in target["env_files"]:
        args += ["--env-file", path]
    return args


def missing_compose_files(target):
    """The compose/env files the container's labels name that are gone."""
    return [path for path in target["config_files"] + target["env_files"]
            if not os.path.exists(path)]


def _stdout(args, stdin=None, timeout=60):
    """Run a command and return its stdout alone (stderr only for the error)."""
    try:
        done = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DockerError(f"timed out after {timeout}s")
    if done.returncode != 0:
        lines = done.stderr.strip().splitlines()
        raise DockerError(lines[-1] if lines else "compose config failed")
    return done.stdout


def config_hashes(target):
    """Service -> config hash, as `docker compose up` would compute it now.

    Compose stamps this hash on each container it creates; a different value
    now means the compose file (or its env) changed since the container
    started, and an update would apply those changes too.

    `config --hash` run straight on the compose file does not match the
    stamped hash for services with an env_file: `up` hashes the service after
    reading the env file into its environment. So the config is rendered first
    and the rendered copy is hashed (fed back on stdin). The rendered copy holds
    env values, so it only ever lives in memory.
    """
    rendered = _stdout(compose_command(target) + ["config"])
    output = _stdout(["docker", "compose", "--ansi", "never", "-p", target["project"],
                      "--project-directory", target["working_dir"], "-f", "-",
                      "config", "--hash", "*"], stdin=rendered)
    hashes = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2:
            hashes[parts[0]] = parts[1]
    return hashes


# --- Health -----------------------------------------------------------------


MAX_HEALTH_WAIT = 900  # seconds; even a very patient healthcheck gets no longer


def _health_budget(healthcheck):
    """Seconds Docker itself may take to call this healthcheck unhealthy.

    Failures during start_period don't count; after it, `retries` failed
    probes in a row (each up to interval + timeout apart) do. Zero or missing
    fields mean Docker's defaults: 30s interval, 30s timeout, 3 retries.
    """
    check = healthcheck or {}

    def seconds(key, default):  # Docker stores these in nanoseconds
        return (check.get(key) or default * 1e9) / 1e9

    interval, timeout = seconds("Interval", 30), seconds("Timeout", 30)
    retries = check.get("Retries") or 3
    return seconds("StartPeriod", 0) + (retries + 1) * (interval + timeout)


def wait_healthy(name, log, timeout=120, interval=3, settle=10):
    """Wait for a freshly started container to prove it works.

    With a healthcheck: healthy is success, unhealthy is failure. While it
    still reports "starting", the wait stretches to however long Docker could
    take to decide (see _health_budget), so a slow starter isn't rolled back
    for being slow.
    Without one: it must stay running, without restarting, for `settle`
    seconds; exited or restarting is failure.
    Returns (ok, message).
    """
    started = time.monotonic()
    deadline = started + timeout
    stable_since = None
    first_restart_count = None
    said, said_at = None, 0
    while time.monotonic() < deadline:
        found = _inspect("container", [name])
        if not found:
            return False, "container disappeared"
        state = found[0]["State"]
        restarts = found[0].get("RestartCount", 0)
        health = (state.get("Health") or {}).get("Status")
        if first_restart_count is None:
            first_restart_count = restarts
            if health is not None:
                budget = _health_budget(found[0]["Config"].get("Healthcheck")) + 30
                deadline = started + min(MAX_HEALTH_WAIT, max(timeout, budget))

        if state.get("Restarting") or restarts != first_restart_count:
            return False, f"container is restarting (exit code {state.get('ExitCode')})"
        if not state.get("Running"):
            return False, f"container {state.get('Status')} (exit code {state.get('ExitCode')})"
        if health == "healthy":
            return True, "healthy"
        if health == "unhealthy":
            return False, "healthcheck reports unhealthy"
        if health is None:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= settle:
                return True, f"running steadily for {settle}s"
        # A long "starting" would print a line every few seconds; say it when
        # it changes and every 30s otherwise.
        waiting = health or "running"
        if waiting != said or time.monotonic() - said_at >= 30:
            log(f"  waiting: {waiting} ({time.monotonic() - started:.0f}s)")
            said, said_at = waiting, time.monotonic()
        time.sleep(interval)
    return False, f"not healthy after {time.monotonic() - started:.0f}s"
