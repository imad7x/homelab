#!/bin/bash
# Daily job (cron): refresh the repo from the live server, run the leak checks, then
# commit, push and upload any changed secret bundles. Nothing is pushed if a check fails.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.."
echo "== $(date '+%F %T')"
exec flock -n /tmp/homelab-sync.lock python3 tools/homelab_sync.py sync --push
