#!/usr/bin/env python3
"""Builds the "Pi-hole Weekly" Grafana dashboard (uid pihole-weekly).

    python3 /docker/monitoring/dashboards-src/pihole_weekly.py [--check]

writes /docker/monitoring/grafana/provisioning/dashboards/pihole-weekly.json, which Grafana
picks up within 10 s. UI edits are allowed but re-running this script overwrites them.
--check also runs every panel query against digest.db for the default week, the current
week and the oldest week, and prints the row counts.

Data source (provisioned from /docker/monitoring/grafana/pihole-digest.yml):
  pihole-digest  SQLite (frser-sqlite-datasource) on digest.db, written weekly by
                 /docker/monitoring/pihole-digest/pihole_digest.py

Everything follows the Week dropdown, not the time picker (which is hidden): "last" is the
last complete Monday-Sunday week, "current" the week so far.
"""
import json
import sqlite3
import sys
from pathlib import Path

OUT = Path("/docker/monitoring/grafana/provisioning/dashboards/pihole-weekly.json")
DIGEST = Path("/docker/monitoring/pihole-digest/digest.db")

DS = {"type": "frser-sqlite-datasource", "uid": "pihole-digest"}
# Two series everywhere, same colour for the same thing: dataviz palette, dark steps
# (Grafana runs dark), validated on Grafana's dark surface.
ALLOWED, BLOCKED = "#3987e5", "#d95926"
GREEN, AMBER, RED = "#0ca30c", "#fab219", "#d03b3b"

# The selected week as a Monday date, for use inside SQL.
WEEK = ("(SELECT CASE '$week' WHEN 'last' THEN (SELECT max(week_start) FROM weeks WHERE complete = 1) "
        "WHEN 'current' THEN (SELECT max(week_start) FROM weeks) ELSE '$week' END)")
SEL = f"WITH sel AS (SELECT {WEEK} AS wk)\n"
DAY_LABEL = ("substr('SunMonTueWedThuFriSat', 1 + 3 * CAST(strftime('%w', day) AS INTEGER), 3) || ' ' || "
             "CAST(strftime('%d', day) AS INTEGER)")
WEEK_LABEL = ("CAST(strftime('%d', week_start) AS INTEGER) || ' ' || "
              "substr('JanFebMarAprMayJunJulAugSepOctNovDec', 1 + 3 * (CAST(strftime('%m', week_start) AS INTEGER) - 1), 3)"
              " || CASE complete WHEN 1 THEN '' ELSE ' (so far)' END")

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


def thresholds(*steps):
    base = [{"color": "text", "value": None}]
    return {"mode": "absolute", "steps": base + [{"color": c, "value": v} for v, c in steps]}


def stat(title, target, unit="none", desc="", decimals=None, change="same_as_value", no_value="0"):
    """One value. With change set, the query returns the week before too and the tile shows the change."""
    defaults = {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": thresholds(),
                "mappings": [], "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc,
            "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "none", "graphMode": "none", "justifyMode": "auto",
                        "orientation": "auto", "textMode": "value", "wideLayout": True,
                        "showPercentChange": change is not None,
                        "percentChangeColorMode": change or "standard"}}


def week_stat(column, title, **kw):
    q = (SEL + f'SELECT CAST(strftime(\'%s\', w.week_start) AS INTEGER) AS time, w.{column} AS "{title}"\n'
         "FROM weeks w, sel WHERE w.week_start IN (sel.wk, date(sel.wk, '-7 days')) ORDER BY w.week_start")
    return stat(title, sql(q), **kw)


def bars(title, target, x_field, desc=""):
    """Stacked Allowed + Blocked bars on a category axis."""
    def color(name, c):
        return {"matcher": {"id": "byName", "options": name},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
    return {"type": "barchart", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"unit": "short", "color": {"mode": "palette-classic"},
                                         "custom": {"fillOpacity": 90, "lineWidth": 0, "gradientMode": "none",
                                                    "axisSoftMin": 0, "axisBorderShow": False,
                                                    "axisGridShow": True},
                                         "noValue": "Nothing yet"},
                            "overrides": [color("Allowed", ALLOWED), color("Blocked", BLOCKED)]},
            "options": {"xField": x_field, "orientation": "vertical", "barWidth": 0.7, "groupWidth": 0.7,
                        "barRadius": 0.1, "showValue": "never", "stacking": "normal", "xTickLabelRotation": 0,
                        "fullHighlight": False,
                        "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi", "sort": "none"}}}


def table(title, target, desc="", overrides=(), no_value="Nothing this week"):
    return {"type": "table", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                    "inspect": False, "filterable": False},
                                         "mappings": [], "thresholds": thresholds(), "noValue": no_value},
                            "overrides": list(overrides)},
            "options": {"showHeader": True, "cellHeight": "sm",
                        "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": []}}}


def ov(name, **props):
    """Field override by column name. props: unit, width, decimals, gauge (bar colour), hidden."""
    p = []
    if "unit" in props:
        p.append({"id": "unit", "value": props["unit"]})
    if "width" in props:
        p.append({"id": "custom.width", "value": props["width"]})
    if "decimals" in props:
        p.append({"id": "decimals", "value": props["decimals"]})
    if "gauge" in props:
        p += [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
              {"id": "color", "value": {"mode": "fixed", "fixedColor": props["gauge"]}}]
    return {"matcher": {"id": "byName", "options": name}, "properties": p}


# ---------------------------------------------------------------- the week in numbers
row("${week:text}")
place(week_stat("queries", "Queries", unit="short", decimals=0,
                desc="Every DNS query Pi-hole answered this week, and the change against the week before."), 0, 3, 4)
place(week_stat("pct_blocked", "Blocked", unit="percent", decimals=1,
                desc="Share of queries answered from the blocklists (gravity, regex, deny lists, special "
                     "domains such as iCloud Private Relay)."), 3, 3, 4)
place(week_stat("pct_cached", "From cache", unit="percent", decimals=1,
                desc="Share answered from Pi-hole's cache, including stale entries served while refreshing."),
      6, 3, 4)
place(week_stat("reply_avg_ms", "Upstream reply", unit="ms", decimals=0, change="inverted",
                desc="Average time the upstream (cloudflared DoH proxy) took to answer forwarded queries. "
                     "Lower is better."), 9, 3, 4)
place(week_stat("clients", "Devices", decimals=0,
                desc="Distinct client addresses. While the router hands out its own address as DNS, every "
                     "device behind it counts as one (the router)."), 12, 3, 4)
place(week_stat("new_sites", "New sites", decimals=0, no_value="Needs 4 weeks",
                desc="Sites (registrable domains) looked up this week that were not seen in the four weeks "
                     "before. Needs four complete weeks of Pi-hole history."), 15, 3, 4)
place(week_stat("bypass_lookups", "Encrypted DNS", decimals=0, change="inverted",
                desc="Lookups of DNS-over-HTTPS / DNS-over-TLS resolver names. A device that finds one can "
                     "resolve around Pi-hole, so ads and trackers are not blocked for it."), 18, 3, 4)
place(week_stat("ptr_private_upstream", "LAN reverse lookups out", decimals=0, change="inverted",
                desc="Reverse lookups of private (LAN, Docker, Tailscale) addresses that Pi-hole forwarded to "
                     "the public upstream. They cannot be answered there and reveal LAN addresses."), 21, 3, 4)
y += 4

place(bars("Day by day", sql(SEL + f'SELECT {DAY_LABEL} AS "Day", queries - blocked AS "Allowed", '
                                   'blocked AS "Blocked"\nFROM days, sel WHERE days.week_start = sel.wk ORDER BY day',
                             time_cols=()),
           "Day", desc="Queries per day of the selected week (Asia/Kolkata days)."), 0, 12, 8)
place(bars("Week by week", sql(f'SELECT {WEEK_LABEL} AS "Week of", queries - blocked AS "Allowed", '
                               'blocked AS "Blocked"\nFROM weeks ORDER BY week_start', time_cols=()),
           "Week of", desc="Every week in the digest. Pi-hole itself keeps 91 days; the digest keeps "
                           "older weeks."), 12, 12, 8)
y += 8

# --------------------------------------------------------------------------- domains
row("Domains")
COUNTS = [ov("#", width=40), ov("Devices", width=70, decimals=0)]
place(table("Top blocked", sql(SEL + 'SELECT rank AS "#", domain AS "Domain", queries AS "Blocked", '
                                     'clients AS "Devices"\nFROM top_domains, sel '
                                     "WHERE week_start = sel.wk AND kind = 'blocked' ORDER BY rank", time_cols=()),
            desc="Most-blocked domains. The router's health checks (www.belkin.com and two reverse lookups) "
                 "are left out of every top list but counted in the totals.",
            overrides=COUNTS + [ov("Blocked", gauge=BLOCKED, width=150)]), 0, 8, 13)
place(table("Top allowed", sql(SEL + 'SELECT rank AS "#", domain AS "Domain", queries AS "Allowed", '
                                     'clients AS "Devices"\nFROM top_domains, sel '
                                     "WHERE week_start = sel.wk AND kind = 'allowed' ORDER BY rank", time_cols=()),
            desc="Most-answered domains that were not blocked (forwarded or from cache).",
            overrides=COUNTS + [ov("Allowed", gauge=ALLOWED, width=150)]), 8, 8, 13)
place(table("New this week", sql(SEL + 'SELECT site AS "Site", queries AS "Queries", blocked AS "Blocked", '
                                       'hosts AS "Hosts", example AS "Busiest host", clients AS "Devices"\n'
                                       'FROM new_sites, sel WHERE week_start = sel.wk ORDER BY rank', time_cols=()),
            desc="Sites first seen this week (not in the four weeks before), busiest first, top 40. Worth a "
                 "glance for new apps, devices or trackers. Reverse lookups are left out.",
            no_value="Needs 4 weeks of history",
            overrides=[ov("Queries", width=80), ov("Blocked", width=70), ov("Hosts", width=55),
                       ov("Devices", width=65)]), 16, 8, 13)
y += 13

# --------------------------------------------------------------------- devices, leaks
row("Devices and leaks")
place(table("Devices", sql(SEL + 'SELECT name AS "Device", ip AS "Address", queries AS "Queries", '
                                 'blocked AS "Blocked", pct_blocked AS "Blocked %"\n'
                                 'FROM clients, sel WHERE week_start = sel.wk ORDER BY rank', time_cols=()),
            desc="Who asked. Until the router hands out Pi-hole (192.168.0.10) as the DNS server, every "
                 "device on the Wi-Fi shows up as the router, LinksysMX5500.",
            overrides=[ov("Address", width=110), ov("Queries", gauge=ALLOWED, width=140),
                       ov("Blocked", width=80), ov("Blocked %", unit="percent", decimals=1, width=85)]),
      0, 10, 9)
place(table("Encrypted DNS and relay lookups",
            sql(SEL + 'SELECT name AS "Device", ip AS "Address", kind AS "Kind", domain AS "Name looked up", '
                      'queries AS "Lookups", blocked AS "Blocked"\nFROM bypass, sel WHERE week_start = sel.wk '
                      'ORDER BY queries DESC', time_cols=()),
            desc="Encrypted DNS resolver: a browser or phone (Secure DNS, Android Private DNS) looking up a DoH/DoT "
                 "server; once connected it bypasses Pi-hole. iCloud Private Relay: Pi-hole answers mask.icloud.com "
                 "with NXDOMAIN so Apple devices fall back to normal DNS. Firefox DoH check: Pi-hole's answer "
                 "tells Firefox not to switch on DoH. Blocked = answered by Pi-hole's blocking.",
            no_value="None this week",
            overrides=[ov("Address", width=110), ov("Kind", width=170), ov("Lookups", width=70),
                       ov("Blocked", width=70)]), 10, 8, 9)
place(table("LAN reverse lookups sent upstream",
            sql(SEL + 'SELECT name AS "Device", ip AS "Address", queries AS "Lookups", '
                      'addresses AS "Addresses"\nFROM ptr_upstream, sel WHERE week_start = sel.wk '
                      'ORDER BY queries DESC', time_cols=()),
            desc="Reverse (PTR) lookups of private addresses that went to the public upstream. Pi-hole answers "
                 "them locally when bogusPriv is on, or asks the router with conditional forwarding.",
            no_value="None this week",
            overrides=[ov("Address", width=110), ov("Lookups", width=70), ov("Addresses", width=80)]),
      18, 6, 9)
y += 9

# ------------------------------------------------------------------------ blocklists
row("Blocklists (as of the last digest run)")
place(table("Lists", sql('SELECT address AS "List", comment AS "Comment", domains AS "Domains", '
                         'invalid AS "Invalid lines", status AS "Last refresh", updated AS "Changed"\n'
                         'FROM lists WHERE enabled = 1 ORDER BY domains DESC', time_cols=("Changed",)),
            desc="Enabled adlists, their size and how the weekly gravity update (Sunday 03:16) went. "
                 "Changed is when the list's content last changed.",
            overrides=[ov("Comment", width=120), ov("Domains", gauge=BLOCKED, width=150),
                       ov("Invalid lines", width=90), ov("Last refresh", width=130),
                       ov("Changed", unit="dateTimeFromNow", width=110)]), 0, 16, 6)
place(stat("Blocked domains", sql('SELECT CAST(value AS INTEGER) AS "Blocked domains" FROM meta '
                                  "WHERE key = 'gravity_domains'", time_cols=()),
           unit="short", decimals=0, change=None, desc="Distinct entries across all lists (gravity)."), 16, 4, 6)
place(stat("Digest updated", sql("SELECT CAST(strftime('%s', value) AS INTEGER) * 1000 AS \"Digest updated\" "
                                 "FROM meta WHERE key = 'updated_at'", time_cols=()),
           unit="dateTimeFromNow", change=None, desc="When pihole_digest.py last ran (cron, daily at 05:10)."), 20, 4, 6)
y += 6

dashboard = {
    "uid": "pihole-weekly",
    "title": "Pi-hole Weekly",
    "description": "Weekly DNS digest from Pi-hole's long-term database: blocking, new sites, devices, "
                   "encrypted-DNS bypass and LAN lookups leaking upstream.",
    "tags": ["pihole", "dns", "homelab"],
    "timezone": "browser",
    "weekStart": "monday",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "",
    "time": {"from": "now-1w/w", "to": "now-1w/w"},
    "timepicker": {"hidden": True},
    "schemaVersion": 41,
    "version": 1,
    "links": [{"title": "Pi-hole", "type": "link", "url": "http://192.168.0.10:8081/admin/", "targetBlank": True,
               "icon": "external link"}],
    "templating": {"list": [{
        "type": "query", "name": "week", "label": "Week", "datasource": DS,
        "query": "SELECT 'last' AS __value, 'Last full week (' || (SELECT label FROM weeks WHERE complete = 1 "
                 "ORDER BY week_start DESC LIMIT 1) || ')' AS __text\n"
                 "UNION ALL SELECT 'current', 'This week so far'\n"
                 "UNION ALL SELECT * FROM (SELECT week_start, label FROM weeks ORDER BY week_start DESC)",
        "definition": "Weeks in the digest",
        "multi": False, "includeAll": False,
        "current": {"text": "Last full week", "value": "last"}, "refresh": 1, "sort": 0, "options": []}]},
    "annotations": {"list": []},
    "panels": panels,
}


def check():
    """Run every panel query against digest.db for three week choices and print row counts."""
    con = sqlite3.connect(f"file:{DIGEST}?mode=ro", uri=True)
    var = dashboard["templating"]["list"][0]["query"]
    weeks = [r[0] for r in con.execute(var)]
    print(f"week dropdown: {len(weeks)} options, first {weeks[:3]}")
    bad = 0
    for choice in ("last", "current", weeks[-1]):
        counts = []
        for p in panels:
            for t in p.get("targets", []):
                rows = con.execute(t["queryText"].replace("$week", choice)).fetchall()
                counts.append(f"{p['title']}={len(rows)}")
                bad += not rows and p["title"] not in ("Encrypted DNS and relay lookups",
                                                       "LAN reverse lookups sent upstream", "New this week")
        print(f"{choice}: " + ", ".join(counts))
    print("all panels return rows" if not bad else f"{bad} panel queries returned nothing")


OUT.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {OUT} ({len(panels)} panels)")
if "--check" in sys.argv:
    check()
