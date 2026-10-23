#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  sonarr-preair-guard.sh
#
#  Throws away any Sonarr grab for an episode that has not aired yet.
#
#  Why this exists. Public trackers carry fake releases named after upcoming
#  episodes ("South Park S29E04 1080p WEB H264-FLUX") days or weeks before
#  the air date. The payload is an .exe/.iso/.7z/.lnk, never video. Sonarr
#  has no "minimum availability" like Radarr, so every RSS sync grabbed one.
#  A real release cannot exist before the episode airs, so anything grabbed
#  more than EARLY_HOURS before air time is fake by definition.
#
#  Hooked as Connect -> Custom Script, On Grab only. The grab has already
#  been sent to qBittorrent when this runs, and the queue item shows up a
#  little later, so the removal runs detached: it waits for the item, then
#  removes it from the client, blocklists it, and skips the re-search.
#
#  Log: /config/logs/preair-guard.log (inside the sonarr container).
# ---------------------------------------------------------------------------
set -u

EARLY_HOURS="${PREAIR_EARLY_HOURS:-12}"   # leeway for air times Sonarr has slightly wrong
API="http://localhost:8989/api/v3"
LOG=/config/logs/preair-guard.log

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
KEY=$(sed -n 's:.*<ApiKey>[[secret:JELLYFIN_APIKEY]]</ApiKey>.*:\1:p' /config/config.xml)

# Detached half: find the queue item for this download and remove it.
if [ "${1:-}" = "--remove" ]; then
    DLID=$2; TITLE=$3
    i=0
    while [ $i -lt 20 ]; do
        curl -s -X POST -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
            -d '{"name":"RefreshMonitoredDownloads"}' "$API/command" > /dev/null
        sleep 15
        QID=$(curl -s -H "X-Api-Key: $KEY" "$API/queue?pageSize=500&includeUnknownSeriesItems=true" |
              jq -r --arg d "$DLID" '.records[] | select((.downloadId // "" | ascii_upcase) == ($d | ascii_upcase)) | .id' | head -1)
        if [ -n "$QID" ]; then
            code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "X-Api-Key: $KEY" \
                "$API/queue/$QID?removeFromClient=true&blocklist=true&skipRedownload=true")
            log "removed + blocklisted (HTTP $code): $TITLE"
            exit 0
        fi
        i=$((i + 1))
    done
    log "WARN gave up waiting for queue item $DLID: $TITLE"
    exit 1
fi

[ "${sonarr_eventtype:-}" = "Grab" ] || exit 0

SERIES=${sonarr_series_id:-}
SEASON=${sonarr_release_seasonnumber:-}
EPS=${sonarr_release_episodenumbers:-}
TITLE=${sonarr_release_title:-?}
DLID=${sonarr_download_id:-}
[ -n "$KEY" ] && [ -n "$SERIES" ] && [ -n "$SEASON" ] && [ -n "$EPS" ] && [ -n "$DLID" ] || exit 0

# Latest air time among the grabbed episodes; "null" if any has no air date (TBA).
LATEST=$(curl -s -H "X-Api-Key: $KEY" "$API/episode?seriesId=$SERIES&seasonNumber=$SEASON" |
         jq -r --arg eps "$EPS" '
           ($eps | split(",") | map(tonumber)) as $want
           | [ .[] | select(.episodeNumber as $n | $want | index($n)) | .airDateUtc ]
           | if length == 0 then empty elif any(. == null) then "null" else max end')
[ -n "$LATEST" ] || exit 0

NOW=$(date -u +%s)
if [ "$LATEST" = "null" ]; then
    REASON="episode has no air date yet"
else
    AIR=$(date -u -d "$LATEST" +%s 2>/dev/null) || exit 0
    [ "$NOW" -lt $((AIR - EARLY_HOURS * 3600)) ] || exit 0
    REASON="airs $LATEST"
fi

log "PRE-AIR grab from ${sonarr_release_indexer:-?} ($REASON): $TITLE"
setsid nohup "$0" --remove "$DLID" "$TITLE" > /dev/null 2>&1 < /dev/null &
exit 0
