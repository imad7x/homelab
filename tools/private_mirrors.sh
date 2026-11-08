#!/bin/bash
# Daily (from sync.sh): copy each self-built app into its clone in ~/github, check it for secrets,
# then commit and push to its PRIVATE repo. The list is ~/.config/homelab-backup/mirrors.txt;
# what is copied is set by mirror-<repo>.filter (optional) and mirror-common.filter there.
#   private_mirrors.sh [--init] [REPO...]   --init creates missing private repos and clones first
# A failed check skips that repo's push only.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
CONF="$HOME/.config/homelab-backup"
OWNER=imad7x
EMAIL="22395452+imad7x@users.noreply.github.com"
init=0; [ "${1:-}" = --init ] && { init=1; shift; }
only=" $* "
rc=0

mirror() {   # mirror REPO SOURCE [DOCS_DIR]
  local name=$1 src=$2 docs=${3:-} dir="$HOME/github/$1" bad=0 stack
  stack=$(echo "$src" | cut -d/ -f1-3)          # /docker/<stack>: its .env values must not leak
  if [ ! -d "$dir/.git" ]; then
    [ $init -eq 1 ] || { echo "$name: no clone at $dir (run with --init)"; rc=1; return; }
    gh repo view "$OWNER/$name" >/dev/null 2>&1 || gh repo create "$OWNER/$name" --private >/dev/null || { rc=1; return; }
    mkdir -p "$dir" && git -C "$dir" init -q -b main && git -C "$dir" remote add origin "https://github.com/$OWNER/$name.git"
    git -C "$dir" config user.name "$OWNER"; git -C "$dir" config user.email "$EMAIL"
    printf '__pycache__/\n*.env\n.env\nnode_modules/\n.next/\nbuild/\n' > "$dir/.gitignore"
  fi
  [ "$(gh repo view "$OWNER/$name" --json visibility -q .visibility 2>/dev/null)" = PRIVATE ] \
    || { echo "$name: repo is not private (or unreachable) - not pushing"; rc=1; return; }
  local f=() ; [ -f "$CONF/mirror-$name.filter" ] && f+=(--filter="merge $CONF/mirror-$name.filter")
  rsync -a --delete "${f[@]}" --filter="merge $CONF/mirror-common.filter" "$src/" "$dir/"
  if [ -n "$docs" ]; then
    mkdir -p "$dir/docs"
    rsync -a --delete --filter="merge $CONF/mirror-docs.filter" "$docs/" "$dir/docs/"
  fi
  while IFS= read -r v; do
    if grep -rqF --exclude-dir=.git -- "$v" "$dir"; then
      echo "$name: a secret value from the stack's .env files is in the repo"; bad=1; break
    fi
  done < <(find "$stack" -maxdepth 3 \( -name '*.env' -o -name .env \) -not -path '*/node_modules/*' \
             -exec grep -hE '^[A-Za-z_][A-Za-z0-9_]*=' {} + 2>/dev/null |
           grep -iE '^[A-Za-z0-9_]*(pass|secret|token|key|pin|cookie|cred|auth|salt|private|jks)[A-Za-z0-9_]*=|=[a-z+]+://[^/:@]*:[^@]+@' |
           cut -d= -f2- | tr -d "\"'" | awk 'length >= 8')
  gitleaks dir "$dir" --no-banner --redact --exit-code 3 --log-level error >/dev/null 2>&1
  [ $? -eq 3 ] && { echo "$name: gitleaks found something (run: gitleaks dir $dir --redact)"; bad=1; }
  if [ $bad -ne 0 ]; then echo "$name: LEAK CHECK FAILED - not pushed"; rc=1; return; fi
  ( cd "$dir" && git add -A &&
    if ! git diff --cached --quiet; then git commit -q -m "Sync from live $(date +%F)" && echo "$name: committed $(git rev-parse --short HEAD)"
    else echo "$name: no changes"; fi &&
    git push -q -u origin main ) || { echo "$name: commit or push failed"; rc=1; }
}

while read -r name src docs; do
  case "$name" in ''|'#'*) continue ;; esac
  [ "$only" = "  " ] || [[ "$only" == *" $name "* ]] || continue
  mirror "$name" "$src" "${docs:-}"
done < "$CONF/mirrors.txt"
exit $rc
