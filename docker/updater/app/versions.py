"""Turn image labels into a short, human version string.

Images announce their version in different labels, so the first one found
wins, in this order:

  1. org.opencontainers.image.version   the standard label
  2. build_version                      LinuxServer's "Linuxserver.io version:- X Build-date:- Y"
  3. org.label-schema.version           the older standard
  4. the image's build date             when nothing announces a version

The full string is kept for a tooltip; the short one drops build noise, so
LinuxServer's "12.0ubu2604-ls48" reads as "12.0".
"""

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "UTC")

_LINUXSERVER = re.compile(r"Linuxserver\.io version:-\s*(\S+)(?:\s+Build-date:-\s*(\S+))?")


def version_info(labels, created):
    """Describe an image's version from its labels and created timestamp.

    Returns {"display": "12.0", "full": "12.0ubu2604-ls48", "date": "2026-09-08"}.
    "display" is what the page shows, "full" goes in the tooltip, and "date"
    is the build date used for "12.0 · new build 2026-09-15".
    """
    labels = labels or {}
    date = local_date(created)
    raw = (
        labels.get("org.opencontainers.image.version")
        or _linuxserver_version(labels.get("build_version"))
        or labels.get("org.label-schema.version")
    )
    if raw:
        raw = raw.strip()
        return {"display": clean(raw), "full": raw, "date": date}
    # Nothing announces a version: the build date is the best we can say.
    return {"display": date or "unknown", "full": f"built {date}" if date else "", "date": date}


def _linuxserver_version(build_version):
    if not build_version:
        return None
    match = _LINUXSERVER.search(build_version)
    return match.group(1) if match else build_version


def clean(raw):
    """Short display form of a version string.

    A leading "v" is always dropped ("v3.5.0" -> "3.5.0"), so the same
    release never shows up as two different strings. Build suffixes go too:
      12.0ubu2604-ls48 -> 12.0    (LinuxServer build number and base OS)
      1.6.0-r1-ls12    -> 1.6.0   (Alpine package release)
      0.25.1.0+7961741 -> 0.25.1.0 (semver build metadata)
    """
    text = re.sub(r"^v(?=\d)", "", raw)
    text = re.sub(r"-ls\d+$", "", text)
    text = re.sub(r"(ubu|deb)\d+$", "", text)
    text = re.sub(r"-r\d+$", "", text)
    text = text.split("+", 1)[0]
    return text or raw


def local_date(created):
    """ "2026-09-08T02:30:49.63347746Z" -> "2026-09-08" in the local time zone."""
    moment = parse_time(created)
    return moment.astimezone(LOCAL_TZ).strftime("%Y-%m-%d") if moment else None


def parse_time(value):
    """Parse the timestamps Docker and registries emit (nanoseconds, "Z")."""
    if not value:
        return None
    # Python reads at most 6 fractional digits; Docker writes up to 9.
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=LOCAL_TZ)


def available_text(running, available):
    """What the "Available" column says for an image that can be updated.

    When a new build carries the same version as the running one (LinuxServer
    rebuilds for base-OS fixes, or an unversioned "latest"), the version alone
    would read as "no change", so the build date is added.
    """
    if not available:
        return None
    if running and available["display"] == running["display"] and available.get("date"):
        if available["display"] == available["date"]:  # only a date to begin with
            return f"new build {available['date']}"
        return f"{available['display']} · new build {available['date']}"
    return available["display"]
