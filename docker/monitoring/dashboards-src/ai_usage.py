#!/usr/bin/env python3
"""Builds the "AI Usage" Grafana dashboard (uid ai-usage).

    python3 /docker/monitoring/dashboards-src/ai_usage.py [--check]

writes /docker/monitoring/grafana/provisioning/dashboards/ai-usage.json, which Grafana
picks up within 10 s. UI edits are allowed but re-running this script overwrites them.
--check runs every panel query against the snapshot for the last 30 days and prints row counts.

Data source (provisioned from /docker/monitoring/grafana/ai-usage.yml):
  ai-usage  SQLite (frser-sqlite-datasource) on a snapshot of the lab AI gateway's ledger
            (/docker/lab-ai), rewritten every 5 minutes. One row per model call in `calls`,
            plan limits over time in `limits`, display names in `pools`.

Pool and plan names come from the `pools` table, never from this file: series are split
with the "Partition by values" transformation and named from the data. Colours follow the
dataviz reference palette's dark steps (Grafana runs dark), matched on those names.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

OUT = Path("/docker/monitoring/grafana/provisioning/dashboards/ai-usage.json")
DB = Path(os.environ.get("AI_USAGE_DB", "/docker/lab-ai/state/grafana/ledger.db"))
DS = {"type": "frser-sqlite-datasource", "uid": "ai-usage"}

BLUE, ORANGE, AQUA = "#3987e5", "#d95926", "#199e70"          # categorical slots 1-3
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"             # status
RANGE = "c.t >= $__from / 1000 AND c.t <= $__to / 1000"
TOKENS_IN = "COALESCE(c.input,0) + COALESCE(c.cache_write,0) + COALESCE(c.cache_read,0)"
WINDOW = "p.label || CASE l.win WHEN '5h' THEN ' · 5-hour' ELSE ' · ' || l.win END"
# Matched on the names in the data: the subscription pool, Google's Gemini pool, Google's other models.
POOL_COLOURS = [(".*Pro.*", ORANGE), (".*Gemini.*", BLUE), (".*GPT.*", AQUA)]

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


def sql(q, time_cols=("time",), ref="A"):
    return {"refId": ref, "datasource": DS, "queryType": "table", "queryText": q, "rawQueryText": q,
            "timeColumns": list(time_cols)}


def colour_overrides(extra=()):
    out = [{"matcher": {"id": "byRegexp", "options": rx},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]} for rx, c in POOL_COLOURS]
    return out + list(extra)


def partition(field):
    return [{"id": "partitionByValues", "options": {"fields": [field], "keepFields": False,
                                                     "naming": {"asLabels": True}}}]


def stat(title, target, unit="none", desc="", decimals=0):
    return {"type": "stat", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "color": {"mode": "thresholds"},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                                         "noValue": "0"}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "none", "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
                        "textMode": "value", "wideLayout": True, "showPercentChange": False}}


def series(title, target, field, unit="none", desc="", bars=False, stack=False, soft_max=None, extra_overrides=()):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 85 if bars else 0,
              "gradientMode": "none", "showPoints": "never" if bars else "auto", "pointSize": 5, "spanNulls": True,
              "lineInterpolation": "stepAfter" if not bars else "linear", "axisBorderShow": False,
              "barAlignment": 0, "axisSoftMin": 0, "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    if soft_max is not None:
        custom["axisSoftMax"] = soft_max
    return {"type": "timeseries", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "transformations": partition(field),
            "fieldConfig": {"defaults": {"unit": unit, "displayName": "${__field.labels." + field + "}",
                                         "color": {"mode": "palette-classic"}, "custom": custom, "noValue": "No data"},
                            "overrides": colour_overrides(extra_overrides)},
            "options": {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def table(title, target, desc="", overrides=()):
    return {"type": "table", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                         "noValue": "Nothing in this range"},
                            "overrides": list(overrides)},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"], "fields": []}}}


def width(name, w):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "custom.width", "value": w}]}


# --- Limits ---------------------------------------------------------------------------------
row("Limits")
left = {"type": "bargauge", "title": "Left in each limit now", "datasource": DS,
        "description": "Newest reading per plan and window, taken every 10 minutes with each CLI's own usage "
                       "command (costs no tokens). The subscription's 5-hour window is shared with chats.",
        "targets": [sql(f"SELECT {WINDOW} AS name, 100 - l.used_pct AS left_pct FROM limits l "
                        "JOIN pools p ON p.code = l.pool "
                        "WHERE l.t = (SELECT max(t) FROM limits x WHERE x.pool = l.pool AND x.win = l.win) "
                        "ORDER BY p.ord, l.win", time_cols=())],
        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "decimals": 0,
                                     "displayName": "${__data.fields.name}",
                                     "color": {"mode": "thresholds"},
                                     "thresholds": {"mode": "absolute", "steps": [
                                         {"color": CRIT, "value": None}, {"color": WARN, "value": 10},
                                         {"color": GOOD, "value": 25}]}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": [], "fields": "", "values": True},
                    "orientation": "horizontal", "displayMode": "basic", "valueMode": "color",
                    "showUnfilled": True, "namePlacement": "left", "sizing": "auto", "minVizHeight": 16,
                    "maxVizHeight": 24}}
place(left, 0, 10, 7)
place(table("When each limit resets", sql(
    f"SELECT {WINDOW} AS \"Limit\", ROUND(l.used_pct, 1) AS \"Used %\", "
    "CASE WHEN l.resets LIKE '____-__-__T%' THEN strftime('%Y-%m-%d %H:%M IST', l.resets, '+330 minutes') "
    "ELSE l.resets END AS \"Resets\" FROM limits l JOIN pools p ON p.code = l.pool "
    "WHERE l.t = (SELECT max(t) FROM limits x WHERE x.pool = l.pool AND x.win = l.win) ORDER BY p.ord, l.win",
    time_cols=()), desc="Times from the subscription CLI are as it prints them; Google's are converted to IST."),
    10, 14, 7)
y += 7
place(series("Limits used over time", sql(
    f"SELECT l.t AS time, {WINDOW} AS series, l.used_pct AS used FROM limits l JOIN pools p ON p.code = l.pool "
    "WHERE l.t >= $__from / 1000 AND l.t <= $__to / 1000 ORDER BY l.t"), "series", unit="percent", soft_max=100,
    desc="Each line is one plan window; it drops back when the window resets.",
    extra_overrides=[{"matcher": {"id": "byRegexp", "options": ".*Pro · week"},
                      "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}}]}]),
    0, 24, 8)
y += 8

# --- Tokens ---------------------------------------------------------------------------------
row("Tokens")
place(stat("Calls", sql(f"SELECT count(*) AS calls FROM calls c WHERE {RANGE}", time_cols=())), 0, 4, 4)
place(stat("Answered from the cache", sql(f"SELECT COALESCE(sum(c.cached), 0) AS hits FROM calls c WHERE {RANGE}",
                                          time_cols=()), desc="Repeat requests that cost no tokens."), 4, 4, 4)
place(stat("Failed calls", sql(f"SELECT COALESCE(sum(1 - c.ok), 0) AS failed FROM calls c WHERE {RANGE}",
                               time_cols=())), 8, 4, 4)
place(stat("Tokens in", sql(f"SELECT COALESCE(sum({TOKENS_IN}), 0) AS tokens FROM calls c WHERE {RANGE}",
                            time_cols=()), unit="short", desc="Fresh input plus cache writes and reads."), 12, 4, 4)
place(stat("Tokens out", sql(f"SELECT COALESCE(sum(COALESCE(c.output,0) + COALESCE(c.thinking,0)), 0) AS tokens "
                             f"FROM calls c WHERE {RANGE}", time_cols=()), unit="short",
           desc="Answers plus thinking."), 16, 4, 4)
place(stat("Helper calls", sql(f"SELECT count(*) AS calls FROM calls c WHERE c.cli = 'agy' AND {RANGE}",
                               time_cols=()), desc="Work handed to the Google plan's models."), 20, 4, 4)
y += 4
place(series("Tokens per day, by pool", sql(
    f"SELECT ((c.t + 19800) / 86400) * 86400 - 19800 AS time, p.label AS pool, "
    f"SUM({TOKENS_IN} + COALESCE(c.output,0) + COALESCE(c.thinking,0)) AS tokens "
    f"FROM calls c JOIN pools p ON p.code = c.pool WHERE {RANGE} GROUP BY 1, 2 ORDER BY 1"), "pool",
    unit="short", bars=True, stack=True, desc="Input, output and thinking tokens per day (IST), stacked by pool."),
    0, 24, 8)
y += 8

# --- Who used what ----------------------------------------------------------------------------
row("Who used what")
place(table("By app and model", sql(
    f"SELECT c.app AS \"App\", c.model AS \"Model\", p.label AS \"Pool\", count(*) AS \"Calls\", "
    f"sum(c.cached) AS \"From cache\", sum({TOKENS_IN}) AS \"Tokens in\", "
    f"sum(COALESCE(c.output,0) + COALESCE(c.thinking,0)) AS \"Tokens out\", "
    f"ROUND(avg(c.ms) / 1000.0, 1) AS \"Avg seconds\", sum(1 - c.ok) AS \"Failed\" "
    f"FROM calls c JOIN pools p ON p.code = c.pool WHERE {RANGE} GROUP BY c.app, c.model ORDER BY 6 DESC",
    time_cols=())), 0, 24, 8)
y += 8
place(table("Recent calls", sql(
    f"SELECT c.t AS time, c.app AS \"App\", c.kind AS \"Job\", c.model AS \"Model\", p.label AS \"Pool\", "
    f"{TOKENS_IN} AS \"In\", COALESCE(c.output,0) + COALESCE(c.thinking,0) AS \"Out\", "
    f"ROUND(c.ms / 1000.0, 1) AS \"Seconds\", "
    f"CASE WHEN c.cached = 1 THEN 'cache' WHEN c.ok = 1 THEN 'ok' ELSE 'failed' END AS \"Result\", "
    f"c.error AS \"Error\" FROM calls c LEFT JOIN pools p ON p.code = c.pool WHERE {RANGE} "
    f"ORDER BY c.t DESC LIMIT 200"), overrides=[width("time", 150), width("App", 80), width("Job", 110),
                                                 width("Result", 70), width("Seconds", 75)]), 0, 24, 10)
y += 10

dashboard = {
    "uid": "ai-usage",
    "title": "AI Usage",
    "description": "Every AI call on the lab, from the gateway's ledger: limits left in each plan, tokens by "
                   "pool, and which app and model used them.",
    "tags": ["ai", "homelab"],
    "timezone": "browser",
    "weekStart": "monday",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "5m",
    "time": {"from": "now-7d", "to": "now"},
    "schemaVersion": 41,
    "version": 1,
    "templating": {"list": []},
    "annotations": {"list": []},
    "panels": panels,
}


def check():
    """Run every query against the snapshot for the last 30 days and print row counts."""
    import time
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    now = int(time.time())
    for p in panels:
        for t in p.get("targets", []):
            q = t["queryText"].replace("$__from", str((now - 30 * 86400) * 1000)).replace("$__to", str(now * 1000))
            rows = con.execute(q).fetchall()
            print(f"{len(rows):5d}  {p['title']}")


OUT.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {OUT} ({len(panels)} panels)")
if "--check" in sys.argv:
    check()
