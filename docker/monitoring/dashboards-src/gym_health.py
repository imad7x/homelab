#!/usr/bin/env python3
"""Builds the "Health & Training" Grafana dashboard (uid gym-health).

    python3 /docker/monitoring/dashboards-src/gym_health.py [--check]

writes /docker/monitoring/grafana/provisioning/dashboards/gym-health.json, which Grafana
picks up within 10 s. UI edits are allowed but re-running this script overwrites them.
--check runs every panel query against health.db for the last 30 days and prints row counts.

Data source (provisioned from /docker/monitoring/grafana/gym-health.yml):
  gym-health  SQLite (frser-sqlite-datasource) on health.db, which the gym app
              (/docker/gym/app/analysis.py) rewrites a minute after new watch data
              and every 15 minutes. Hours the watch was off are missing rows or NULLs,
              never zeros, so lines break and averages skip them.

Everything follows the time picker (default: last 30 days). Colours: the dataviz
reference palette's dark steps (Grafana runs dark); status colours only for recovery.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

OUT = Path("/docker/monitoring/grafana/provisioning/dashboards/gym-health.json")
DB = Path(os.environ.get("GYM_HEALTH_DB", "/docker/gym/state/grafana/health.db"))
DS = {"type": "frser-sqlite-datasource", "uid": "gym-health"}

BLUE, ORANGE, AQUA, YELLOW = "#3987e5", "#d95926", "#199e70", "#c98500"     # categorical slots 1-4
GREY = "#898781"                                                            # de-emphasis
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"                          # status
RANGE = "time >= $__from / 1000 AND time <= $__to / 1000"   # Grafana's range, in ms

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


def color(name, c, regex=False, **extra):
    props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]
    for k, v in extra.items():
        props.append({"id": k, "value": v})
    return {"matcher": {"id": "byRegexp" if regex else "byName", "options": name}, "properties": props}


def stat(title, target, unit="none", desc="", decimals=None, mappings=(), color_mode="none", no_value="–"):
    defaults = {"unit": unit, "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                "mappings": list(mappings), "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
                        "textMode": "value", "wideLayout": True, "showPercentChange": False}}


def series(title, targets, unit="none", desc="", overrides=(), bars=False, stack=False, decimals=None,
           threshold=None, soft_min=None, transformations=(), points=False, legend=True, relative=None):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 85 if bars else 10,
              "gradientMode": "none", "showPoints": "always" if points else "never", "pointSize": 6,
              "spanNulls": False, "insertNulls": 2 * 86400 * 1000 if not points else False,
              "lineInterpolation": "linear", "axisBorderShow": False, "barAlignment": 0,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    if soft_min is not None or bars:
        custom["axisSoftMin"] = 0 if soft_min is None else soft_min
    defaults = {"unit": unit, "color": {"mode": "fixed", "fixedColor": BLUE}, "custom": custom, "noValue": "No data"}
    if decimals is not None:
        defaults["decimals"] = decimals
    if threshold is not None:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": "transparent", "value": None},
                                                                {"color": GREY, "value": threshold}]}
        custom["thresholdsStyle"] = {"mode": "line"}
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": DS, "targets": list(targets),
         "fieldConfig": {"defaults": defaults, "overrides": list(overrides)},
         "transformations": list(transformations),
         "options": {"legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "none"}}}
    if relative:
        p["timeFrom"] = relative
    return p


def table(title, target, desc="", overrides=()):
    return {"type": "table", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                         "noValue": "Nothing in this range"},
                            "overrides": list(overrides)},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"], "fields": []}}}


def ov(name, **props):
    p = []
    if "unit" in props:
        p.append({"id": "unit", "value": props["unit"]})
    if "width" in props:
        p.append({"id": "custom.width", "value": props["width"]})
    if "decimals" in props:
        p.append({"id": "decimals", "value": props["decimals"]})
    return {"matcher": {"id": "byName", "options": name}, "properties": p}


READY_MAP = [{"type": "value", "options": {
    "3": {"text": "Good to train", "color": GOOD, "index": 0},
    "2": {"text": "Normal", "color": BLUE, "index": 1},
    "1": {"text": "Take it easier", "color": WARN, "index": 2},
    "0": {"text": "Rest today", "color": CRIT, "index": 3}}}]
LATEST = "SELECT time, {col} AS \"{name}\" FROM days WHERE {col} IS NOT NULL AND time <= $__to / 1000 ORDER BY time DESC LIMIT 1"

# --- Today --------------------------------------------------------------------------------
row("Latest")
place(stat("Recovery", sql(LATEST.format(col="readiness_score", name="Recovery")), mappings=READY_MAP,
           color_mode="background",
           desc="HRV 7-day average vs your normal range (60 days before), resting HR vs its 30-day average, and "
                "last night's sleep. Good / Normal = train as planned; Take it easier = go lighter; Rest = resting HR "
                "up two days running with low HRV. Guidance from a wrist sensor, not a diagnosis."), 0, 4, 4)
place(stat("Last night's sleep", sql(LATEST.format(col="sleep_h", name="Sleep")), unit="suffix: h", decimals=1,
           desc="Hours asleep in the main sleep that ended that morning. Nights under 3 h of recorded sleep don't count."), 4, 4, 4)
place(stat("Resting HR", sql(LATEST.format(col="rhr", name="Resting HR")), unit="bpm"), 8, 3, 4)
place(stat("Usual resting HR", sql(LATEST.format(col="rhr_base", name="Usual")), unit="bpm", decimals=1,
           desc="Average of the 30 days before."), 11, 3, 4)
place(stat("HRV, 7-day", sql(LATEST.format(col="hrv7", name="HRV 7-day")), unit="ms", decimals=0,
           desc="7-day average of nightly HRV (rMSSD)."), 14, 3, 4)
place(stat("Active minutes this week", sql(
    "SELECT max(time) AS time, sum(coalesce(mod_min, 0) + 2 * coalesce(vig_min, 0)) AS \"WHO minutes\" FROM days "
    "WHERE time >= CAST(strftime('%s', date('now', '+330 minutes', 'weekday 0', '-6 days')) AS INTEGER) - 19800"),
    desc="Moderate (64-76% of max HR) plus vigorous (77%+) x 2, Monday to now. WHO: 150-300 a week."), 17, 4, 4)
place(stat("Data updated", sql("SELECT CAST(strftime('%s', value) AS INTEGER) * 1000 AS \"Updated\" FROM meta "
                               "WHERE key = 'updated_at'", time_cols=()), unit="dateTimeFromNow",
           desc="When the gym app last rewrote health.db (a minute after new data, and every 15 min)."), 21, 3, 4)
y += 4

# --- Recovery -----------------------------------------------------------------------------
row("Recovery")
place(series("Resting heart rate", [sql(f'SELECT time, rhr AS "Resting HR", rhr_base AS "Usual (30-day)" FROM days WHERE {RANGE} ORDER BY time')],
             unit="bpm", decimals=0, overrides=[color("Usual (30-day)", GREY)],
             desc="Drifting down over months = fitter. 5+ above usual for a few days often means poor sleep, stress or illness."),
      0, 12, 8)
place(series("HRV (rMSSD)", [sql(f'SELECT time, hrv AS "Each night", hrv7 AS "7-day average", hrv_lo AS "Normal low", '
                                 f'hrv_hi AS "Normal high" FROM days WHERE {RANGE} ORDER BY time')],
             unit="ms", decimals=0,
             overrides=[color("Each night", GREY, **{"custom.drawStyle": "points", "custom.pointSize": 5}),
                        color("Normal low", GREY, **{"custom.lineWidth": 1}),
                        color("Normal high", GREY, **{"custom.lineWidth": 1, "custom.fillBelowTo": "Normal low",
                                                      "custom.fillOpacity": 12})],
             desc="Your normal range is the mean +/- half a standard deviation of ln(rMSSD) over the 60 days before. "
                  "A 7-day average below it means recover more."),
      12, 12, 8)
y += 8
place({"type": "state-timeline", "title": "Recovery by day", "datasource": DS,
       "targets": [sql(f'SELECT time, readiness_score AS "Recovery" FROM days WHERE {RANGE} ORDER BY time')],
       "fieldConfig": {"defaults": {"mappings": READY_MAP, "color": {"mode": "thresholds"},
                                    "thresholds": {"mode": "absolute", "steps": [{"color": GREY, "value": None}]},
                                    "custom": {"fillOpacity": 85, "lineWidth": 0}, "noValue": "Building baseline"},
                       "overrides": []},
       "options": {"showValue": "never", "rowHeight": 0.8, "mergeValues": False, "alignValue": "left",
                   "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                   "tooltip": {"mode": "single", "sort": "none"}}}, 0, 24, 4)
y += 4

# --- Sleep ---------------------------------------------------------------------------------
row("Sleep")
place(series("Sleep stages per night", [sql(f'SELECT time, deep_h AS "Deep", light_h AS "Light", rem_h AS "REM", awake_h AS "Awake" '
                                            f'FROM days WHERE {RANGE} ORDER BY time')],
             unit="suffix: h", decimals=1, bars=True, stack=True, threshold=7,
             overrides=[color("Deep", BLUE), color("Light", ORANGE), color("REM", AQUA), color("Awake", YELLOW)],
             desc="Credited to the morning you woke up. Nights without the watch are blank. Line = 7 h."), 0, 14, 8)
place(table("Recent nights", sql(
    f"SELECT date AS Night, sleep_h AS Asleep, substr(time(bedtime + 19800, 'unixepoch'), 1, 5) AS Bed, "
    f"substr(time(waketime + 19800, 'unixepoch'), 1, 5) AS Wake, round(100.0 * deep_h / sleep_h) AS \"Deep %\", "
    f"round(100.0 * rem_h / sleep_h) AS \"REM %\", hrv AS HRV, spo2_min AS \"SpO2 low\" "
    f"FROM days WHERE {RANGE} AND sleep_h IS NOT NULL ORDER BY time DESC", time_cols=()),
    overrides=[ov("Asleep", unit="suffix: h", decimals=1), ov("HRV", unit="ms", decimals=0), ov("SpO2 low", unit="percent")]),
      14, 10, 8)
y += 8

# --- Activity ------------------------------------------------------------------------------
row("Activity")
place(series("Steps per day", [sql(f'SELECT time, steps_valid AS "Full day", steps_partial AS "Partial day (not averaged)" '
                                   f'FROM days WHERE {RANGE} ORDER BY time')],
             unit="short", bars=True, stack=True, threshold=8000,
             overrides=[color("Partial day (not averaged)", GREY)],
             desc="A day counts only with 10+ hours worn between 7:00 and 23:00; partial days are grey and left out "
                  "of averages. Line = 8,000."), 0, 12, 8)
place(series("Hours worn per day", [sql(f'SELECT time, wear_h AS "Worn (24 h)", wake_wear_h AS "Worn 7:00-23:00" '
                                        f'FROM days WHERE {RANGE} ORDER BY time')],
             unit="suffix: h", decimals=1, overrides=[color("Worn 7:00-23:00", GREY)], points=True,
             desc="A 15-minute slot counts as worn if it has a heart-rate reading or steps."), 12, 12, 8)
y += 8
place(series("Heart rate (last 2 days)", [sql(f'SELECT time, bpm AS "Heart rate" FROM hr WHERE {RANGE} ORDER BY time')],
             unit="bpm", decimals=0, relative="2d", legend=False,
             desc="Per minute. Gaps are the times the watch was off your wrist."), 0, 16, 8)
panels[-1]["fieldConfig"]["defaults"]["custom"]["insertNulls"] = 15 * 60 * 1000
place(series("Active minutes per week", [sql(f'SELECT time, activity_min AS "WHO minutes" FROM weeks WHERE {RANGE} ORDER BY time')],
             unit="short", bars=True, threshold=150, legend=False,
             desc="Moderate + vigorous x 2, from heart rate. Line = 150 (WHO minimum)."), 16, 8, 8)
y += 8

# --- Training ------------------------------------------------------------------------------
row("Training")
place(series("Days trained per week", [sql(f'SELECT time, days_trained AS "Days trained" FROM weeks WHERE {RANGE} ORDER BY time')],
             bars=True, threshold=5, legend=False, decimals=0, desc="Lifting sessions and Cult classes. Line = the 5-day weekly goal."),
      0, 8, 8)
place(series("Hard sets per week", [sql(f'SELECT time, hard_sets AS "Hard sets" FROM weeks WHERE {RANGE} ORDER BY time')],
             bars=True, legend=False, decimals=0, desc="Ticked working sets in the gym app."), 8, 8, 8)
place(series("Heart-rate load per day (TRIMP)", [sql(f'SELECT time, trimp AS "Load" FROM days WHERE {RANGE} ORDER BY time')],
             bars=True, legend=False, decimals=0,
             desc="Edwards' TRIMP: minutes in heart-rate zones 1-5 weighted 1-5. Lifting reads low on heart rate."), 16, 8, 8)
y += 8
LIFTS = (("Barbell Bench Press", "Bench press", BLUE), ("Barbell Back Squat", "Back squat", ORANGE),
         ("Romanian Deadlift (RDL)", "Romanian deadlift", AQUA), ("Lat Pulldown", "Lat pulldown", YELLOW))
place(series("Main lifts: estimated 1-rep max", [sql(
    "SELECT time, " + ", ".join(f"max(CASE WHEN exercise = '{name}' THEN e1rm END) AS \"{label}\"" for name, label, _ in LIFTS)
    + f" FROM lifts WHERE {RANGE} GROUP BY time ORDER BY time")],
             unit="kg", decimals=1, points=True, overrides=[color(label, c) for _, label, c in LIFTS],
             desc="Epley estimate from the best set of each session."), 0, 12, 8)
panels[-1]["fieldConfig"]["defaults"]["custom"]["showPoints"] = "always"
panels[-1]["fieldConfig"]["defaults"]["custom"]["fillOpacity"] = 0
panels[-1]["fieldConfig"]["defaults"]["custom"]["insertNulls"] = False
panels[-1]["fieldConfig"]["defaults"]["custom"]["spanNulls"] = True
place(table("Workouts", sql(
    f"SELECT date AS Date, coalesce(name, type) AS Workout, minutes AS Min, avg_hr AS \"Avg HR\", max_hr AS \"Max HR\", "
    f"trimp AS Load, z1 AS Z1, z2 AS Z2, z3 AS Z3, z4 AS Z4, z5 AS Z5 FROM workouts WHERE {RANGE} ORDER BY time DESC",
    time_cols=()), desc="Watch-recorded and logged sessions, merged when they overlap. Z1-Z5: minutes in each zone."),
      12, 12, 8)
y += 8

# --- Body ----------------------------------------------------------------------------------
row("Body")
place(series("Weight", [sql(f'SELECT time, weight AS "Weigh-in", weight7 AS "7-day average" FROM days WHERE {RANGE} ORDER BY time')],
             unit="kg", decimals=1,
             overrides=[color("Weigh-in", GREY, **{"custom.drawStyle": "points", "custom.pointSize": 5})],
             desc="In a recomp the scale can sit still while you lose fat and gain muscle; watch the trend with your lifts."),
      0, 24, 7)
panels[-1]["fieldConfig"]["defaults"]["custom"]["spanNulls"] = True
y += 7

dashboard = {
    "uid": "gym-health",
    "title": "Health & Training",
    "description": "Huawei watch data via Gadgetbridge and the gym app's logged workouts: recovery against your own "
                   "baseline, sleep, activity with wear-time gaps, training load and lifts.",
    "tags": ["health", "gym", "homelab"],
    "timezone": "browser",
    "weekStart": "monday",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "15m",
    "time": {"from": "now-30d", "to": "now"},
    "schemaVersion": 41,
    "version": 1,
    "links": [{"title": "Gym app", "type": "link", "url": "http://192.168.0.10:8086/#/health", "targetBlank": True,
               "icon": "external link"}],
    "templating": {"list": []},
    "annotations": {"list": []},
    "panels": panels,
}


def check():
    """Run every query against health.db for the last 30 days and print row counts."""
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
