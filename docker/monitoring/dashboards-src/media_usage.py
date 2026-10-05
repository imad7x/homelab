#!/usr/bin/env python3
"""Builds the "Media Usage" Grafana dashboard (uid media-usage).

    python3 /docker/monitoring/dashboards-src/media_usage.py

writes /docker/monitoring/grafana/provisioning/dashboards/media-usage.json, which Grafana
picks up within 10 s. UI edits are allowed but re-running this script overwrites them.

Data sources (all provisioned from /docker/monitoring/grafana/*.yml):
  jellystat-pg  Jellystat's Postgres, read through the report.* functions
                (/docker/jellyfin/jellystat/report.sql)
  seerr-api / radarr-api / sonarr-api   Infinity JSON datasources
  Prometheus    cAdvisor network counters for jellyfin / qbittorrent
"""
import json
from pathlib import Path

OUT = Path("/docker/monitoring/grafana/provisioning/dashboards/media-usage.json")

PG = {"type": "grafana-postgresql-datasource", "uid": "jellystat-pg"}
PROM = {"type": "prometheus", "uid": "PBFA97CFB590B2093"}
SEERR = {"type": "yesoreyeram-infinity-datasource", "uid": "seerr-api"}
RADARR = {"type": "yesoreyeram-infinity-datasource", "uid": "radarr-api"}
SONARR = {"type": "yesoreyeram-infinity-datasource", "uid": "sonarr-api"}

SEERR_URL = "http://192.168.0.10:5055/api/v1"
RADARR_URL = "http://192.168.0.10:7878/api/v3"
SONARR_URL = "http://192.168.0.10:8989/api/v3"

GREEN, AMBER, RED = "#0ca30c", "#fab219", "#d03b3b"
MIXED = {"type": "datasource", "uid": "-- Mixed --"}
WHEN = "time: D MMM YYYY, HH:mm"

# report.plays() rows for the dashboard time range, narrowed by the User dropdown.
PLAYS = "report.plays($__timeFrom(), $__timeTo())"
USERS = "('__all' IN ($user) OR username IN ($user))"
IST_DAY = "date_trunc('day', at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'"
HOURS = "round(sum(seconds) / 3600.0, 1)"

_ids = iter(range(1, 1000))
panels = []
y = 0


def place(panel, x, w, h):
    panel["id"] = next(_ids)
    panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
    panels.append(panel)
    return panel


def row(title):
    global y
    panels.append({"type": "row", "id": next(_ids), "title": title, "collapsed": False,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
    y += 1


def sql(q, fmt="table", ref="A"):
    return {"refId": ref, "datasource": PG, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": q}


def prom(expr, legend, ref="A", instant=False):
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend,
            "range": not instant, "instant": instant}


def infinity(ds, url, columns, root="", ref="A"):
    return {"refId": ref, "datasource": ds, "type": "json", "source": "url", "format": "table",
            "parser": "backend", "url": url, "root_selector": root, "url_options": {"method": "GET"},
            "columns": [{"selector": s, "text": t, "type": ty} for s, t, ty in columns]}


def thresholds(*steps):
    base = [{"color": "text", "value": None}]
    return {"mode": "absolute", "steps": base + [{"color": c, "value": v} for v, c in steps]}


def stat(title, target, unit="none", desc="", decimals=None, field=None, calc="lastNotNull",
         steps=(), no_value="0", color_mode="none"):
    defaults = {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": thresholds(*steps),
                "mappings": [], "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc,
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": [calc], "fields": field or "", "values": False},
                        "colorMode": color_mode, "graphMode": "none", "justifyMode": "auto",
                        "orientation": "auto", "textMode": "value", "wideLayout": True,
                        "showPercentChange": False, "percentChangeColorMode": "standard"}}


def timeseries(title, targets, unit, desc="", bars=False, stack=True, legend_calcs=("sum",)):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1,
              "fillOpacity": 80 if bars else 45, "gradientMode": "none", "showPoints": "never",
              "pointSize": 5, "lineInterpolation": "smooth", "spanNulls": False,
              "axisBorderShow": False, "axisPlacement": "auto", "axisLabel": "",
              "axisCenteredZero": False, "axisSoftMin": 0, "barAlignment": 0, "barWidthFactor": 0.8,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "thresholdsStyle": {"mode": "off"},
              "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "scaleDistribution": {"type": "linear"}, "insertNulls": False}
    return {"type": "timeseries", "title": title, "description": desc,
            "datasource": targets[0]["datasource"], "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom,
                                         "color": {"mode": "palette-classic"}},
                            "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                                   "calcs": list(legend_calcs)},
                        "tooltip": {"mode": "multi", "sort": "desc", "hideZeros": True}}}


def table(title, targets, desc="", overrides=(), transformations=(), sort=None, footer=None):
    opts = {"showHeader": True, "cellHeight": "sm",
            "footer": {"show": bool(footer), "reducer": ["sum"], "countRows": False,
                       "fields": footer or []}}
    if sort:
        opts["sortBy"] = [{"displayName": sort, "desc": True}]
    ds = targets[0]["datasource"] if len({t["datasource"]["uid"] for t in targets}) == 1 else MIXED
    return {"type": "table", "title": title, "description": desc,
            "datasource": ds, "targets": targets,
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                    "inspect": False, "filterable": True},
                                         "mappings": [], "thresholds": thresholds()},
                            "overrides": list(overrides)},
            "options": opts, "transformations": list(transformations)}


def bargauge(title, target, unit, desc="", decimals=None):
    d = {"unit": unit, "color": {"mode": "palette-classic"}, "min": 0, "noValue": "Nothing yet",
         "thresholds": thresholds()}
    if decimals is not None:
        d["decimals"] = decimals
    return {"type": "bargauge", "title": title, "description": desc,
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                        "orientation": "horizontal", "displayMode": "gradient", "valueMode": "color",
                        "namePlacement": "left", "showUnfilled": True, "sizing": "manual",
                        "minVizHeight": 18, "maxVizHeight": 26, "minVizWidth": 8,
                        "text": {"titleSize": 13, "valueSize": 15}, "legend": {"showLegend": False}}}


def pie(title, target, unit, desc=""):
    return {"type": "piechart", "title": title, "description": desc,
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                         "noValue": "Nothing yet"}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                        "pieType": "donut", "displayLabels": ["percent"],
                        "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                                   "values": ["value", "percent"]},
                        "tooltip": {"mode": "single", "sort": "none"}}}


def barchart(title, target, x_field, unit, desc=""):
    return {"type": "barchart", "title": title, "description": desc,
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                         "custom": {"fillOpacity": 80, "lineWidth": 0, "gradientMode": "none",
                                                    "axisSoftMin": 0, "axisBorderShow": False},
                                         "noValue": "Nothing yet"},
                            "overrides": [{"matcher": {"id": "byName", "options": x_field},
                                           "properties": [{"id": "unit", "value": "string"}]}]},
            "options": {"xField": x_field, "orientation": "vertical", "barWidth": 0.8, "groupWidth": 0.7,
                        "showValue": "never", "stacking": "none", "xTickLabelRotation": 0,
                        "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "single", "sort": "none"}}}


def ov(name, **props):
    """Field override by column name. props: unit, width, mappings, cell (cellOptions), links, decimals."""
    p = []
    if "unit" in props:
        p.append({"id": "unit", "value": props["unit"]})
    if "width" in props:
        p.append({"id": "custom.width", "value": props["width"]})
    if "decimals" in props:
        p.append({"id": "decimals", "value": props["decimals"]})
    if "mappings" in props:
        p.append({"id": "mappings", "value": props["mappings"]})
    if "cell" in props:
        p.append({"id": "custom.cellOptions", "value": props["cell"]})
    if "minmax" in props:
        p += [{"id": "min", "value": props["minmax"][0]}, {"id": "max", "value": props["minmax"][1]}]
    if "thresholds" in props:
        p.append({"id": "thresholds", "value": props["thresholds"]})
    if "hidden" in props:
        p.append({"id": "custom.hidden", "value": True})
    return {"matcher": {"id": "byName", "options": name}, "properties": p}


def text_map(pairs):
    return [{"type": "value", "options": {str(k): {"text": t, "color": c, "index": i}
                                          for i, (k, t, c) in enumerate(pairs)}}]


# ---------------------------------------------------------------- Right now
row("Right now")
place(stat("Streaming now", sql("SELECT count(*) FROM report.now_playing()"),
           desc="Jellyfin sessions playing or paused right now (from Jellystat's live monitor).",
           steps=[(1, GREEN)], color_mode="value"), 0, 4, 4)
place(stat("Transcoding now", sql("SELECT count(*) FROM report.now_playing() WHERE method = 'Transcode'"),
           desc="Sessions the server is converting on the fly. Transcoding costs CPU; direct play costs nothing.",
           steps=[(1, AMBER)], color_mode="value"), 4, 4, 4)
place(stat("Jellyfin streaming out",
           prom('sum(rate(container_network_transmit_bytes_total{name="jellyfin"}[2m])) * 8', "Jellyfin", instant=True),
           unit="bps", desc="Network traffic leaving the Jellyfin container (streams to your devices).",
           decimals=1), 8, 4, 4)
place(stat("qBittorrent downloading",
           prom('sum(rate(container_network_receive_bytes_total{name="qbittorrent"}[2m])) * 8', "Download", instant=True),
           unit="bps", desc="Download speed of qBittorrent right now.", decimals=1), 12, 4, 4)
place(stat("qBittorrent uploading",
           prom('sum(rate(container_network_transmit_bytes_total{name="qbittorrent"}[2m])) * 8', "Upload", instant=True),
           unit="bps", desc="Upload (seeding) speed of qBittorrent right now. Capped at 6 MB/s in qBittorrent.",
           decimals=1), 16, 4, 4)
place(stat("Last played",
           sql("SELECT extract(epoch FROM max(at)) * 1000 AS \"Last played\" FROM report.plays(now() - interval '20 years', now())"),
           unit="dateTimeFromNow", desc="When anything was last watched or listened to on Jellyfin.",
           no_value="Never"), 20, 4, 4)
y += 4
place(table("Now playing",
            [sql("""SELECT username AS "User", title AS "Title", episode AS "Episode", client AS "App",
       device AS "Device", method AS "Playback", progress AS "Progress",
       CASE WHEN paused THEN 'Paused' ELSE 'Playing' END AS "State"
  FROM report.now_playing() ORDER BY started""")],
            desc="Live sessions. Refreshes with the dashboard (every minute).",
            overrides=[ov("Progress", unit="percent", minmax=(0, 100), width=180,
                          cell={"type": "gauge", "mode": "basic", "valueDisplayMode": "text"},
                          thresholds=thresholds((0, "#3274d9"))),
                       ov("Playback", width=120, mappings=text_map(
                           [("Transcode", "Transcode", AMBER), ("Direct play", "Direct play", GREEN),
                            ("Direct stream", "Direct stream", GREEN)]),
                          cell={"type": "color-text"}),
                       ov("State", width=90)]), 0, 24, 5)
y += 5

# ---------------------------------------------------------------- Watching
row("Watching (selected time range)")
place(stat("Plays", sql(f"SELECT count(*) FROM {PLAYS} WHERE {USERS}"),
           desc="Playback sessions in the time range (anything under 30 s is ignored)."), 0, 6, 4)
place(stat("Watch time", sql(f"SELECT {HOURS} FROM {PLAYS} WHERE {USERS}"), unit="suffix: h", decimals=1,
           desc="Hours watched or listened in the time range."), 6, 6, 4)
place(stat("People watching", sql(f"SELECT count(DISTINCT username) FROM {PLAYS} WHERE {USERS}"),
           desc="Different Jellyfin users who played something."), 12, 6, 4)
place(stat("Different titles", sql(f"SELECT count(DISTINCT title) FROM {PLAYS} WHERE {USERS}"),
           desc="Distinct movies, series and albums/tracks played (a series counts once)."), 18, 6, 4)
y += 4
place(timeseries("Watch time per day",
                 [sql(f"""SELECT {IST_DAY} AS time, username AS metric, {HOURS} AS value
  FROM {PLAYS} WHERE {USERS} GROUP BY 1, 2 ORDER BY 1""", fmt="time_series")],
                 unit="suffix: h", bars=True, desc="Hours per day (India time), stacked by user."), 0, 16, 8)
panels[-1]["fieldConfig"]["defaults"]["decimals"] = 1
place(pie("Movies vs TV vs music",
          sql(f"""SELECT kind AS "Type", {HOURS} AS "Hours" FROM {PLAYS} WHERE {USERS}
 GROUP BY 1 ORDER BY 2 DESC"""), unit="suffix: h",
          desc="Share of watch time by kind of content."), 16, 8, 8)
y += 8
place(bargauge("Watch time by user",
               sql(f"""SELECT username AS "User", {HOURS} AS "Hours" FROM {PLAYS} WHERE {USERS}
 GROUP BY 1 ORDER BY 2 DESC"""), unit="suffix: h", decimals=1), 0, 8, 7)
place(barchart("Time of day",
               sql(f"""SELECT lpad(h::text, 2, '0') AS "Hour",
       coalesce((SELECT {HOURS} FROM {PLAYS}
                  WHERE {USERS} AND extract(hour FROM at AT TIME ZONE 'Asia/Kolkata') = h), 0) AS "Hours"
  FROM generate_series(0, 23) AS h ORDER BY h"""), "Hour", "suffix: h",
               desc="When people watch, by hour of the day (India time)."), 8, 8, 7)
place(barchart("Day of week",
               sql(f"""SELECT to_char(date '2024-01-01' + (d - 1), 'Dy') AS "Day",
       coalesce((SELECT {HOURS} FROM {PLAYS}
                  WHERE {USERS} AND extract(isodow FROM at AT TIME ZONE 'Asia/Kolkata') = d), 0) AS "Hours"
  FROM generate_series(1, 7) AS d ORDER BY d"""), "Day", "suffix: h",
               desc="Watch time by weekday (India time)."), 16, 8, 7)
y += 7

# ---------------------------------------------------------------- What's watched
row("What's watched")
place(table("Top movies",
            [sql(f"""SELECT title AS "Movie", count(*) AS "Plays", {HOURS} AS "Hours",
       string_agg(DISTINCT username, ', ') AS "Watched by", max(at) AS "Last watched"
  FROM {PLAYS} WHERE {USERS} AND kind = 'Movies' GROUP BY 1 ORDER BY 3 DESC LIMIT 25""")],
            overrides=[ov("Last watched", unit="dateTimeFromNow", width=110), ov("Plays", width=62), ov("Watched by", width=115),
                       ov("Hours", width=70, decimals=1)]), 0, 8, 10)
place(table("Top series",
            [sql(f"""SELECT title AS "Series", count(*) AS "Episodes", {HOURS} AS "Hours",
       string_agg(DISTINCT username, ', ') AS "Watched by", max(at) AS "Last watched"
  FROM {PLAYS} WHERE {USERS} AND kind = 'TV' GROUP BY 1 ORDER BY 3 DESC LIMIT 25""")],
            desc="Episodes = episode plays in the range (rewatches count again).",
            overrides=[ov("Last watched", unit="dateTimeFromNow", width=110), ov("Episodes", width=85), ov("Watched by", width=115),
                       ov("Hours", width=70, decimals=1)]), 8, 8, 10)
place(table("People",
            [sql(f"""SELECT username AS "User", count(*) AS "Plays", {HOURS} AS "Hours",
       mode() WITHIN GROUP (ORDER BY client) AS "Usual app",
       mode() WITHIN GROUP (ORDER BY kind) AS "Mostly", max(at) AS "Last seen"
  FROM {PLAYS} WHERE {USERS} GROUP BY 1 ORDER BY 3 DESC""")],
            overrides=[ov("Last seen", unit="dateTimeFromNow", width=110), ov("Plays", width=70),
                       ov("Hours", width=70, decimals=1)]), 16, 8, 10)
y += 10
place(table("Watch history",
            [sql(f"""SELECT at AS "When", username AS "User", kind AS "Type", title AS "Title",
       episode AS "Episode", client AS "App", device AS "Device", method AS "Playback",
       seconds AS "Watched"
  FROM {PLAYS} WHERE {USERS} ORDER BY at DESC LIMIT 500""")],
            desc="Every playback session in the range, newest first (max 500). Columns are filterable.",
            overrides=[ov("When", unit=WHEN, width=150), ov("Watched", unit="dtdurations", width=150),
                       ov("Type", width=80), ov("User", width=100),
                       ov("Playback", width=120, cell={"type": "color-text"}, mappings=text_map(
                           [("Transcode", "Transcode", AMBER), ("Direct play", "Direct play", GREEN),
                            ("Direct stream", "Direct stream", GREEN)]))]), 0, 24, 11)
y += 11

# ---------------------------------------------------------------- Apps & playback quality
row("Apps, devices & playback")
place(bargauge("Apps",
               sql(f"""SELECT coalesce(client, 'Unknown') AS "App", {HOURS} AS "Hours" FROM {PLAYS}
 WHERE {USERS} GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""), unit="suffix: h", decimals=1,
               desc="Watch time by Jellyfin app (Moonfin, web, Android, ...)."), 0, 6, 8)
place(table("Devices",
            [sql(f"""SELECT coalesce(device, 'Unknown') AS "Device", count(*) AS "Plays", {HOURS} AS "Hours",
       max(at) AS "Last used"
  FROM {PLAYS} WHERE {USERS} GROUP BY 1 ORDER BY 3 DESC LIMIT 20""")],
            overrides=[ov("Last used", unit="dateTimeFromNow", width=110), ov("Plays", width=70),
                       ov("Hours", width=70, decimals=1)]), 6, 6, 8)
place(pie("Direct play vs transcode",
          sql(f"""SELECT method AS "Playback", count(*) AS "Plays" FROM {PLAYS} WHERE {USERS}
 GROUP BY 1 ORDER BY 2 DESC"""), unit="none",
          desc="Direct play = the file is sent as-is (best). Transcode = the server converts it live."),
      12, 6, 8)
place(table("Why it transcoded",
            [sql(f"""SELECT reason AS "Reason", count(*) AS "Plays"
  FROM {PLAYS} p, unnest(string_to_array(p.transcode_reasons, ', ')) AS reason
 WHERE {USERS} AND p.method = 'Transcode' GROUP BY 1 ORDER BY 2 DESC""")],
            desc="Recorded from 30 Sep 2026 on (the imported plugin history has no reasons). "
                 "Jellyfin's reasons for transcoding, e.g. VideoCodecNotSupported = that device can't "
                 "play the file's format; ContainerBitrateExceedsLimit = a bitrate limit is set in the app.", overrides=[ov("Plays", width=70)]), 18, 6, 8)
y += 8

# ---------------------------------------------------------------- Library
row("Library")
lib = [
    ("Movies", """SELECT count(*) FROM jf_library_items WHERE "Type" = 'Movie' AND NOT coalesce(archived, false)""", "none"),
    ("Series", """SELECT count(*) FROM jf_library_items WHERE "Type" = 'Series' AND NOT coalesce(archived, false)""", "none"),
    ("Episodes", """SELECT count(*) FROM jf_library_episodes WHERE NOT coalesce(archived, false)""", "none"),
    ("Music tracks", """SELECT count(*) FROM jf_library_items WHERE "Type" = 'Audio' AND NOT coalesce(archived, false)""", "none"),
    ("Library size", """SELECT sum("Size") FROM jf_item_info""", "bytes"),
    ("Added in range", "SELECT count(*) FROM report.library_added($__timeFrom(), $__timeTo())", "none"),
]
for i, (t, q, u) in enumerate(lib):
    place(stat(t, sql(q), unit=u, decimals=1 if u == "bytes" else None,
               desc="Size of all media files Jellyfin knows about." if u == "bytes" else ""), i * 4, 4, 4)
y += 4
place(timeseries("Added to the library per week",
                 [sql(f"""SELECT date_trunc('week', added AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata' AS time,
       kind AS metric, count(*) AS value
  FROM report.library_added($__timeFrom(), $__timeTo()) GROUP BY 1, 2 ORDER BY 1""", fmt="time_series")],
                 unit="none", bars=True, desc="New movies, episodes and tracks by the week Jellyfin added them."),
      0, 16, 8)
place(table("Libraries",
            [sql("""SELECT "Name" AS "Library", coalesce("CollectionType", '') AS "Kind",
       item_count AS "Items", nullif(episode_count, 0) AS "Episodes"
  FROM jf_libraries WHERE NOT coalesce(archived, false) ORDER BY 3 DESC""")],
            overrides=[ov("Kind", width=90), ov("Items", width=70), ov("Episodes", width=90)]), 16, 8, 8)
y += 8

# ---------------------------------------------------------------- Requests
row("Requests (Seerr)")
count_cols = [("total", "total", "number"), ("pending", "pending", "number"),
              ("processing", "processing", "number"), ("completed", "completed", "number"),
              ("movie", "movie", "number"), ("tv", "tv", "number")]
reqs = [("All requests", "total", "Every request ever made in Seerr."),
        ("Waiting for approval", "pending", "Requests an admin still has to approve."),
        ("Downloading", "processing", "Approved and being searched for / downloaded."),
        ("Done", "completed", "Requests that are now in the library."),
        ("Movie requests", "movie", ""), ("Series requests", "tv", "")]
for i, (t, f, d) in enumerate(reqs):
    p = stat(t, infinity(SEERR, f"{SEERR_URL}/request/count", count_cols), field=f"/^{f}$/", desc=d,
             steps=[(1, AMBER)] if f == "pending" else (), color_mode="value" if f == "pending" else "none")
    place(p, i * 4, 4, 4)
y += 4
place(bargauge("Requests by user",
               infinity(SEERR, f"{SEERR_URL}/user?take=100",
                        [("displayName", "User", "string"), ("requestCount", "Requests", "number")], root="results"),
               unit="none", desc="All-time request count per Seerr user."), 0, 6, 9)

STATUS_MAP = text_map([(1, "Unknown", "text"), (2, "Pending", AMBER), (3, "Downloading", "#3274d9"),
                       (4, "Partly available", "#8ab8ff"), (5, "Available", GREEN),
                       (6, "Blocklisted", RED), (7, "Deleted", RED)])
# After a join Grafana labels fields "By A", "Movie B" (query refIds); strip that back off.
STRIP_REF = {"id": "renameByRegex", "options": {"regex": "^(.+) [AB]$", "renamePattern": "$1"}}
# Rows that exist only on the Radarr/Sonarr side of the join (added there directly) are dropped.
NOT_REQUESTED = {"id": "filterByValue", "options": {"type": "exclude", "match": "any", "filters": [
    {"fieldName": "Requested", "config": {"id": "isNull", "options": {}}}]}}


def REMOVED(col):
    """Requests whose title has since been deleted from Radarr/Sonarr (e.g. by MediaCleaner)."""
    return {"matcher": {"id": "byName", "options": col},
            "properties": [{"id": "noValue", "value": "Removed from library"}]}


REQ_COLS = [("createdAt", "Requested", "timestamp"), ("requestedBy.displayName", "By", "string"),
            ("media.status", "Status", "number")]
req_over = [ov("Requested", unit="dateTimeFromNow", width=120), ov("By", width=90),
            ov("Status", width=120, mappings=STATUS_MAP, cell={"type": "color-text"})]
place(table("Latest movie requests",
            [infinity(SEERR, f"{SEERR_URL}/request?take=100&sort=added&mediaType=movie",
                      REQ_COLS + [("media.tmdbId", "tmdb", "number")], root="results", ref="A"),
             infinity(RADARR, f"{RADARR_URL}/movie",
                      [("tmdbId", "tmdb", "number"), ("title", "Movie", "string"), ("year", "Year", "number")],
                      ref="B")],
            desc="Seerr requests, titled via Radarr. Status is Seerr's view of the movie. "
                 "Removed from library = the movie was deleted since.",
            transformations=[{"id": "joinByField", "options": {"byField": "tmdb", "mode": "outerTabular"}},
                             STRIP_REF, NOT_REQUESTED,
                             {"id": "organize", "options": {"excludeByName": {"tmdb": True},
                                                            "indexByName": {"Requested": 0, "Movie": 1, "Year": 2,
                                                                            "By": 3, "Status": 4},
                                                            "renameByName": {"Requested": "Requested", "Movie": "Movie",
                                                                             "Year": "Year", "By": "By",
                                                                             "Status": "Status"}}},
                             {"id": "sortBy", "options": {"sort": [{"field": "Requested", "desc": True}]}}],
            overrides=req_over + [ov("Year", width=60, decimals=0, unit="none"), REMOVED("Movie")]), 6, 9, 9)
place(table("Latest series requests",
            [infinity(SEERR, f"{SEERR_URL}/request?take=100&sort=added&mediaType=tv",
                      REQ_COLS + [("media.tmdbId", "tmdb", "number")], root="results", ref="A"),
             infinity(SONARR, f"{SONARR_URL}/series",
                      [("tmdbId", "tmdb", "number"), ("title", "Series", "string")], ref="B")],
            desc="Seerr requests, titled via Sonarr. Removed from library = the series was deleted since.",
            transformations=[{"id": "joinByField", "options": {"byField": "tmdb", "mode": "outerTabular"}},
                             STRIP_REF, NOT_REQUESTED,
                             {"id": "organize", "options": {"excludeByName": {"tmdb": True},
                                                            "indexByName": {"Requested": 0, "Series": 1,
                                                                            "By": 2, "Status": 3},
                                                            "renameByName": {"Requested": "Requested", "Series": "Series",
                                                                             "By": "By", "Status": "Status"}}},
                             {"id": "sortBy", "options": {"sort": [{"field": "Requested", "desc": True}]}}],
            overrides=req_over + [REMOVED("Series")]), 15, 9, 9)
y += 9

# ---------------------------------------------------------------- Downloads
row("Downloads")
place(timeseries("Network: downloads, seeding and streaming",
                 [prom('sum(rate(container_network_receive_bytes_total{name="qbittorrent"}[$__rate_interval])) * 8',
                       "qBittorrent download", "A"),
                  prom('sum(rate(container_network_transmit_bytes_total{name="qbittorrent"}[$__rate_interval])) * 8',
                       "qBittorrent upload", "B"),
                  prom('sum(rate(container_network_transmit_bytes_total{name="jellyfin"}[$__rate_interval])) * 8',
                       "Jellyfin streaming", "C")],
                 unit="bps", stack=False, legend_calcs=("mean", "max"),
                 desc="From cAdvisor's per-container network counters."), 0, 12, 8)
HIST_FROM = "${__from:date:iso}"
mov_hist = infinity(RADARR, f"{RADARR_URL}/history/since?date={HIST_FROM}&eventType=downloadFolderImported&includeMovie=true",
                    [("date", "Imported", "timestamp"), ("movie.title", "Movie", "string"),
                     ("quality.quality.name", "Quality", "string"), ("data.size", "Size", "number"),
                     ("sourceTitle", "Release", "string")])
ep_hist = infinity(SONARR, f"{SONARR_URL}/history/since?date={HIST_FROM}&eventType=downloadFolderImported&includeSeries=true&includeEpisode=true",
                   [("date", "Imported", "timestamp"), ("series.title", "Series", "string"),
                    ("episode.seasonNumber", "S", "number"), ("episode.episodeNumber", "E", "number"),
                    ("episode.title", "Episode", "string"), ("quality.quality.name", "Quality", "string"),
                    ("data.size", "Size", "number")])
place(stat("Movies downloaded", mov_hist, field="/^Movie$/", calc="count",
           desc="Movies Radarr imported in the time range."), 12, 4, 4)
place(stat("Episodes downloaded", ep_hist, field="/^Series$/", calc="count",
           desc="Episodes Sonarr imported in the time range."), 16, 4, 4)
p = stat("Downloaded size", mov_hist, unit="bytes", decimals=1, field="/^Size$/", calc="sum",
         desc="Total size of everything Radarr and Sonarr imported in the time range.")
p["targets"] = [dict(mov_hist, refId="A"), dict(ep_hist, refId="B")]
p["datasource"] = MIXED
p["transformations"] = [{"id": "merge", "options": {}}]
place(p, 20, 4, 4)
y += 4
place(stat("Seeding ratio",
           prom('sum(increase(container_network_transmit_bytes_total{name="qbittorrent"}[$__range])) / '
                'sum(increase(container_network_receive_bytes_total{name="qbittorrent"}[$__range]))', "ratio", instant=True),
           decimals=2, desc="Uploaded ÷ downloaded by qBittorrent over the time range."), 12, 4, 4)
place(stat("Torrent data in",
           prom('sum(increase(container_network_receive_bytes_total{name="qbittorrent"}[$__range]))', "rx", instant=True),
           unit="bytes", decimals=1, desc="Everything qBittorrent received in the time range."), 16, 4, 4)
place(stat("Streamed by Jellyfin",
           prom('sum(increase(container_network_transmit_bytes_total{name="jellyfin"}[$__range]))', "tx", instant=True),
           unit="bytes", decimals=1, desc="Everything Jellyfin sent to devices in the time range."), 20, 4, 4)
y += 4
size_over = [ov("Imported", unit=WHEN, width=150), ov("Size", unit="bytes", decimals=1, width=90),
             ov("Quality", width=110)]
place(table("Movies downloaded", [mov_hist], sort="Imported",
            transformations=[{"id": "organize", "options": {"indexByName": {"Imported": 0, "Movie": 1, "Quality": 2,
                                                                           "Size": 3, "Release": 4}}}],
            overrides=size_over + [ov("Movie", width=220)]), 0, 12, 9)
place(table("Episodes downloaded", [ep_hist], sort="Imported",
            transformations=[{"id": "organize", "options": {"indexByName": {"Imported": 0, "Series": 1, "S": 2, "E": 3,
                                                                           "Episode": 4, "Quality": 5, "Size": 6}}}],
            overrides=size_over + [ov("S", width=40, decimals=0, unit="none"),
                                   ov("E", width=40, decimals=0, unit="none"), ov("Series", width=150),
                                   ov("Episode", width=170)]),
      12, 12, 9)
y += 9

dashboard = {
    "uid": "media-usage",
    "title": "Media Usage",
    "description": "Who watches what on Jellyfin (Jellystat), Seerr requests, and downloads.",
    "tags": ["jellyfin", "media", "homelab"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "time": {"from": "now-30d", "to": "now"},
    "schemaVersion": 41,
    "version": 1,
    "links": [{"title": "Jellystat", "type": "link", "url": "http://192.168.0.10:3003", "targetBlank": True,
               "icon": "external link"},
              {"title": "Seerr", "type": "link", "url": "http://192.168.0.10:5055", "targetBlank": True,
               "icon": "external link"}],
    "templating": {"list": [{
        "type": "query", "name": "user", "label": "User", "datasource": PG,
        "query": "SELECT DISTINCT username FROM report.plays(now() - interval '20 years', now()) "
                 "WHERE username IS NOT NULL ORDER BY 1",
        "definition": "Jellyfin users with any plays",
        "multi": True, "includeAll": True, "allValue": "'__all'",
        "current": {"text": ["All"], "value": ["$__all"]}, "refresh": 2, "sort": 1, "options": []}]},
    "annotations": {"list": []},
    "panels": panels,
}
OUT.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {OUT} ({len(panels)} panels)")
