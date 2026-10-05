#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  jellyfin-prewarm.sh
#
#  Makes Jellyfin extract embedded subtitle tracks and font attachments up
#  front, instead of during playback.
#
#  Why this exists. Measured on this box, a cold ASS subtitle extraction costs
#  ~2.5 s and dumping a release's font attachments another ~2 s - 21 separate
#  ffmpeg spawns for one Death Note episode. Once extracted, the same request
#  serves from cache in ~9 ms. That 4-5 s is dead time before the first frame,
#  and it is paid AGAIN on every subtitle switch, because the webOS client
#  tears down its HLS session and starts a new one each time.
#
#  One subtitle request extracts every subtitle stream in the file, so a
#  single call per item covers all tracks. Attachments are fetched by their
#  real stream index, which is not zero-based - it comes from the API.
#
#  Modes:
#    --all            every movie and episode; skips items already warm
#    --path <file>    one media file, path as Jellyfin sees it (/data/...)
#    --id <itemId>    one item by Jellyfin id
#    (no args)        reads sonarr_*/radarr_* env vars, for use as a
#                     Connect -> Custom Script on import
#
#  Config: prewarm.env beside this script, or JF_URL/JF_KEY in the
#  environment. JF_CACHE optionally points at Jellyfin's data/data directory;
#  when readable, --all uses it to skip warm items without calling the API.
# ---------------------------------------------------------------------------
set -u

SELF_DIR=$(dirname "$0")
[ -f "$SELF_DIR/prewarm.env" ] && . "$SELF_DIR/prewarm.env"

JF_URL="${JF_URL:-http://192.168.0.10:8096}"
JF_KEY="${JF_KEY:-}"
JF_CACHE="${JF_CACHE:-}"
DELAY="${PREWARM_DELAY:-0.2}"      # pause between items in --all, to be kind to the USB disks

[ -n "$JF_KEY" ] || { echo "ERROR: JF_KEY not set (see prewarm.env)" >&2; exit 1; }
AUTH="Authorization: MediaBrowser Token=\"$JF_KEY\""

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }

api() { curl -sf -m 120 -H "$AUTH" "$JF_URL$1" 2>/dev/null; }
hit() { curl -s -o /dev/null -m 300 -w '%{http_code}' -H "$AUTH" "$JF_URL$1" 2>/dev/null; }

# Jellyfin caches subtitles/attachments under a dash-separated form of the id.
dashed() { printf '%s-%s-%s-%s-%s' "$(echo "$1"|cut -c1-8)" "$(echo "$1"|cut -c9-12)" \
           "$(echo "$1"|cut -c13-16)" "$(echo "$1"|cut -c17-20)" "$(echo "$1"|cut -c21-32)"; }

sub_ext() {
    case "$1" in
        ass|ssa)  echo ass ;;
        subrip|srt|text) echo srt ;;
        webvtt|vtt) echo vtt ;;
        *) echo srt ;;
    esac
}

# prewarm_item <itemId> - returns 0 if work was done, 1 if nothing to do
prewarm_item() {
    _id=$1
    # NOTE: the response goes to a file, never a shell variable. `echo "$var"`
    # in dash expands backslash escapes, and at least one stream Title in this
    # library is "\r\n                " - echoing that produces a real newline
    # and jq then rejects the JSON as malformed.
    _f=$(mktemp)
    api "/Items?ids=$_id&fields=MediaSources" > "$_f"
    [ -s "$_f" ] || { log "  ! could not fetch item $_id"; rm -f "$_f"; return 1; }

    _ms=$(jq -r '.Items[0].MediaSources[0].Id // empty' "$_f")
    [ -n "$_ms" ] || { rm -f "$_f"; return 1; }

    # extracting any one subtitle stream makes Jellyfin extract them all,
    # so a single request per item is enough
    _sidx=$(jq -r '[.Items[0].MediaSources[0].MediaStreams[]?
              | select(.Type=="Subtitle" and (.IsExternal|not) and .IsTextSubtitleStream==true)]
              | first | .Index // empty' "$_f")
    _scodec=$(jq -r '[.Items[0].MediaSources[0].MediaStreams[]?
              | select(.Type=="Subtitle" and (.IsExternal|not) and .IsTextSubtitleStream==true)]
              | first | .Codec // empty' "$_f")
    # attachment indices are the mkv stream indices, not 0..n-1
    _atts=$(jq -r '[.Items[0].MediaSources[0].MediaAttachments[]?.Index | tostring] | join(" ")' "$_f")
    rm -f "$_f"

    [ -z "$_sidx" ] && [ -z "$_atts" ] && return 1

    _did=0
    if [ -n "$_sidx" ]; then
        _ext=$(sub_ext "$_scodec")
        _rc=$(hit "/Videos/$_id/$_ms/Subtitles/$_sidx/0/Stream.$_ext")
        if [ "$_rc" = "200" ]; then _did=1; else log "  ! subtitle idx $_sidx -> HTTP $_rc"; fi
    fi
    if [ -n "$_atts" ]; then
        for _a in $_atts; do
            hit "/Videos/$_id/$_ms/Attachments/$_a" >/dev/null
        done
        _did=1
    fi
    return $(( 1 - _did ))
}

# is_warm <itemId> <needsSubs 0|1> <attCount> - only usable when JF_CACHE is readable
is_warm() {
    [ -n "$JF_CACHE" ] && [ -d "$JF_CACHE" ] || return 1
    _d=$(dashed "$1")
    _p=$(echo "$1" | cut -c1-2)
    if [ "$2" = "1" ] && [ ! -d "$JF_CACHE/subtitles/$_p/$_d" ]; then return 1; fi
    if [ "$3" -gt 0 ] && [ ! -d "$JF_CACHE/attachments/$_p/$_d" ]; then return 1; fi
    return 0
}

find_id_by_path() {
    # Jellyfin's searchTerm matches item NAMES, not filenames, so a release
    # filename never matches. Pull the path listing instead and match exactly;
    # it is ~530 KB / 0.1 s for this library, which is fine for an import hook.
    _p=$1
    _u=$(api "/Users" | jq -r '.[0].Id')
    api "/Items?userId=$_u&recursive=true&includeItemTypes=Movie,Episode&fields=Path&enableImages=false&limit=10000" \
        | jq -r --arg p "$_p" '.Items[]? | select(.Path==$p) | .Id' | head -1
}

run_all() {
    _u=$(api "/Users" | jq -r '.[0].Id')
    log "== full prewarm pass =="
    _tmp=$(mktemp); trap 'rm -f "$_tmp"' EXIT
    api "/Items?userId=$_u&recursive=true&includeItemTypes=Movie,Episode&fields=MediaSources&limit=10000" \
      | jq -r '.Items[]? | . as $i
               | ($i.MediaSources[0] // {}) as $m
               | [ $i.Id,
                   ( [ $m.MediaStreams[]? | select(.Type=="Subtitle" and (.IsExternal|not) and .IsTextSubtitleStream==true) ] | length | if .>0 then 1 else 0 end ),
                   ( [ $m.MediaAttachments[]?.Index ] | length ),
                   ( $i.Name // "?" ) ] | @tsv' > "$_tmp"

    _total=$(wc -l < "$_tmp"); _n=0; _warm=0; _done=0; _skip=0
    log "$_total items in library"
    while IFS="$(printf '\t')" read -r id needsub natt name; do
        _n=$((_n+1))
        if [ "$needsub" = "0" ] && [ "$natt" = "0" ]; then _skip=$((_skip+1)); continue; fi
        if is_warm "$id" "$needsub" "$natt"; then _warm=$((_warm+1)); continue; fi
        if prewarm_item "$id"; then
            _done=$((_done+1))
            log "[$_n/$_total] warmed: $(printf '%s' "$name" | cut -c1-45) (subs=$needsub att=$natt)"
            sleep "$DELAY"
        fi
    done < "$_tmp"
    log "== done: $_done warmed, $_warm already warm, $_skip nothing to extract =="
}

case "${1:-}" in
    --all)  run_all ;;
    --id)   prewarm_item "$2" && log "warmed $2" || log "nothing to do for $2" ;;
    --path) id=$(find_id_by_path "$2"); [ -n "$id" ] || { log "no Jellyfin item for $2"; exit 0; }
            prewarm_item "$id" && log "warmed $2" || log "nothing to do for $2" ;;
    "")
        # Sonarr / Radarr Custom Script hook
        ev="${sonarr_eventtype:-${radarr_eventtype:-}}"
        case "$ev" in
            Test) log "prewarm hook reachable"; exit 0 ;;
            Download|DownloadFolderImported) : ;;
            *) exit 0 ;;
        esac
        p="${sonarr_episodefile_path:-${radarr_moviefile_path:-}}"
        [ -n "$p" ] || exit 0
        # Jellyfin's realtime monitor needs a moment to notice the new file
        i=0; while [ $i -lt 12 ]; do
            id=$(find_id_by_path "$p")
            [ -n "$id" ] && break
            i=$((i+1)); sleep 10
        done
        [ -n "${id:-}" ] || { log "timed out waiting for Jellyfin to index $p"; exit 0; }
        prewarm_item "$id" && log "warmed $p" || log "nothing to extract for $p"
        ;;
    *) echo "usage: $0 [--all | --path <file> | --id <itemId>]" >&2; exit 2 ;;
esac
