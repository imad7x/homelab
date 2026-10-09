#!/bin/sh
# Mounted into /docker-entrypoint.d/, so the image's entrypoint starts it just before
# nginx. Reloads nginx when the dashboard rewrites the blocklist; a reload that fails
# the config test leaves the running config alone.
(
    sum() { cat /etc/nginx/blocklist/*.conf 2>/dev/null | md5sum; }
    last=$(sum)
    while sleep 5; do
        now=$(sum)
        [ "$now" = "$last" ] && continue
        last=$now
        if nginx -t -q; then
            nginx -s reload && echo "blocklist changed: nginx reloaded"
        else
            echo "blocklist changed but the config test failed; kept the running config" >&2
        fi
    done
) </dev/null &
