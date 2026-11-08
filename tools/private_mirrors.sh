#!/bin/bash
# Daily (from sync.sh): copy stacks that have their own PRIVATE repo into their clones in ~/github,
# check them for secrets, then commit and push. The rsync filters (what is copied) live in
# ~/.config/homelab-backup/mirror-<repo>.filter. A failed check skips that repo's push only.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
CONF="$HOME/.config/homelab-backup"
rc=0

mirror() {   # mirror REPO SOURCE [DOCS_DIR]
  local name=$1 src=$2 docs=${3:-} dir="$HOME/github/$1" bad=0
  [ -d "$dir/.git" ] || { echo "$name: no clone at $dir"; rc=1; return; }
  [ "$(gh repo view "imad7x/$name" --json visibility -q .visibility 2>/dev/null)" = PRIVATE ] \
    || { echo "$name: repo is not private (or unreachable) - not pushing"; rc=1; return; }
  rsync -a --delete --filter="merge $CONF/mirror-$name.filter" "$src/" "$dir/"
  if [ -n "$docs" ]; then
    mkdir -p "$dir/docs"
    rsync -a --delete --filter="merge $CONF/mirror-$name-docs.filter" "$docs/" "$dir/docs/"
  fi
  # every secret-looking value in the stack's .env files must not appear in the copy
  while IFS= read -r v; do
    if grep -rqF --exclude-dir=.git -- "$v" "$dir"; then
      echo "$name: a value from the stack's .env file is in the repo"; bad=1; break
    fi
  done < <(find "$src" -maxdepth 2 \( -name '*.env' -o -name .env \) -exec grep -hE '^[A-Za-z_][A-Za-z0-9_]*=' {} + \
           | cut -d= -f2- | tr -d "\"'" | awk 'length >= 8')
  gitleaks dir "$dir" --no-banner --redact --exit-code 3 --log-level error >/dev/null 2>&1
  [ $? -eq 3 ] && { echo "$name: gitleaks found something (run: gitleaks dir $dir --redact)"; bad=1; }
  if [ $bad -ne 0 ]; then echo "$name: LEAK CHECK FAILED - not pushed"; rc=1; return; fi
  cd "$dir" || return
  git add -A
  if ! git diff --cached --quiet; then
    git commit -q -m "Sync from live $(date +%F)" && echo "$name: committed $(git rev-parse --short HEAD)"
  else
    echo "$name: no changes"
  fi
  git push -q -u origin main || { echo "$name: push failed"; rc=1; }
}

mirror website /docker/landing
mirror 7xjobs /docker/jobs "$HOME/projects/jobs"
exit $rc
