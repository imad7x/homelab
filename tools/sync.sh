#!/bin/bash
# Daily job (cron): refresh the public homelab repo from the live server (leak checks first,
# nothing is pushed if one fails), then mirror the stacks that have their own private repo
# (tools/private_mirrors.sh). Any failure sends one phone notification through core7x.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.."
echo "== $(date '+%F %T')"
exec 9>/tmp/homelab-sync.lock
flock -n 9 || { echo "another sync is running"; exit 0; }

out=$(mktemp); trap 'rm -f "$out"' EXIT
python3 tools/homelab_sync.py sync --push 2>&1 | tee -a "$out"; a=${PIPESTATUS[0]}
tools/private_mirrors.sh --init 2>&1 | tee -a "$out"; b=${PIPESTATUS[0]}
[ "$a" -eq 0 ] && [ "$b" -eq 0 ] && exit 0

tok=$(grep -m1 '^NOTIFY_TOKEN=' /docker/lab-ai/portal/secrets.env 2>/dev/null | cut -d= -f2-)
if [ -n "$tok" ]; then
  body=$(grep -E 'FAILED|failed|not pushed|not private|gitleaks|no clone|^  ' "$out" | head -8)
  python3 -c 'import json,sys; print(json.dumps({"source":"github-sync","title":"GitHub sync failed",
    "body":sys.argv[1] + "\nLog: ~/.local/state/homelab-sync.log","priority":4,"tags":["warning"],
    "dedupe_key":"github-sync"}))' "$body" |
  curl -s -m 5 -o /dev/null -X POST http://192.168.0.10:8093/api/notify -H 'Content-Type: application/json' \
    -H 'X-Portal: 1' -H "X-Notify-Token: $tok" --data-binary @- || true
fi
exit 1
