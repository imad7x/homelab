#!/usr/bin/env python3
"""GTA6 - VIctory performance dashboard (gta_* metrics pushed by /docker/lab-ai/pc/gtamon collect.ps1).
Writes ../grafana/provisioning/dashboards/gta6-victory.json."""
import json, pathlib
DS = {"type": "prometheus", "uid": "PBFA97CFB590B2093"}
panels, y = [], 0
def tgt(expr, legend): return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + len(legend) % 26)}
def stat(title, expr, unit, x, w=3, thr=None, dec=1, desc=""):
    steps = thr or [{"color": "text", "value": None}]
    panels.append({"type": "stat", "title": title, "description": desc, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": 4},
        "targets": [dict(tgt(expr, title), refId="A")], "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": dec, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []}})
def ts(title, targets, unit, x, w, h=8, desc="", stack=False, minv=None):
    t = [dict(tgt(e, l), refId=chr(65 + i)) for i, (e, l) in enumerate(targets)]
    d = {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 10, "showPoints": "never", "spanNulls": 10000,
         "stacking": {"mode": "normal" if stack else "none"}}}
    if minv is not None: d["min"] = minv
    panels.append({"type": "timeseries", "title": title, "description": desc, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": t, "fieldConfig": {"defaults": d, "overrides": []}, "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}})
def R(c): return [{"color": c[0], "value": None}] + [{"color": col, "value": v} for v, col in c[1:]]
L = lambda m: f"last_over_time({m}[30s])"
stat("Game", L("gta_running"), "none", 0, 2, R(["red", (1, "green")]), 0)
stat("FPS", L("gta_fps_title"), "none", 2, 3, R(["red", (20, "orange"), (28, "green")]), 1, "Frame rate of the last second (VIctory window title)")
stat("FPS low (5 min)", "min_over_time(gta_fps_title_min[5m])", "none", 5, 3, R(["red", (15, "orange"), (25, "green")]), 1, "Lowest one-second frame rate in the last 5 minutes")
stat("Frame time", L("gta_v_ms"), "ms", 8, 3, R(["green", (40, "orange"), (55, "red")]), 1, "VIctory's own average over its last 5 s")
stat("Bad dips (run)", L("gta_bad_dips_total"), "none", 11, 3, R(["green", (1, "orange"), (10, "red")]), 0,
     "Seconds whose frame rate fell under 45% of the last minute's median (a screenshot is taken, at most one per 90 s)")
stat("Game RAM", L("gta_private_gb"), "decgbytes", 14, 3, R(["green", (24, "orange"), (28, "red")]), 1, "Private memory of victory.exe; the PC has 32 GB")
stat("Free RAM", L("gta_avail_gb"), "decgbytes", 17, 2, R(["red", (1.5, "orange"), (3, "green")]), 1)
stat("VRAM", L("gta_vram_gb"), "decgbytes", 19, 2, R(["green", (14, "orange"), (15.5, "red")]), 1, "Dedicated video memory used by the game (16 GB card)")
stat("Errors", L("gta_error_total"), "none", 21, 3, R(["green", (1, "red")]), 0, "ERROR/panic/device-removed lines in the game's output this run")
y = 4
ts("Frame rate", [("gta_fps_title", "fps (each second)"), ("gta_fps_title_min", "lowest second in sample"), ("gta_v_fps", "fps (layer, 5 s avg)")], "none", 0, 12, minv=0)
ts("Frame time breakdown (layer, 5 s avg)", [("gta_v_ms", "frame"), ("gta_v_cpu_ms", "CPU preparing"), ("gta_v_gpu_ms", "GPU"),
   ("gta_v_copy_ms", "copies"), ("gta_v_tex_ms", "textures"), ("gta_v_back_ms", "copying back")], "ms", 12, 12, minv=0,
   desc="What sets the frame: when 'CPU preparing' is above 'GPU', the submit thread is the limit (see the repo README)")
y = 12
ts("Stutter: dips per minute", [("increase(gta_dips_total[1m])", "dips (<70% of median)"), ("increase(gta_bad_dips_total[1m])", "bad dips (<45%)")], "none", 0, 8, minv=0)
ts("Shader pipelines & draws", [("gta_v_pipes_making", "pipelines being made"), ("gta_v_dropped", "draws dropped (no pipeline yet)"), ("gta_v_draws / 100", "draws/frame ÷100")], "none", 8, 8, minv=0,
   desc="Pipelines being made cause first-run stutter; `victory warm --used` makes them ahead")
ts("Uploads & streaming", [("gta_v_copy_mb", "copies MB/frame"), ("gta_v_tex_mb", "textures MB/frame"), ("gta_v_back_mb", "copied back MB/frame"), ("gta_disk_read_mb_s / 30", "disk read MB/s ÷30")], "none", 16, 8, minv=0)
y = 20
ts("Memory", [("gta_private_gb", "game private"), ("gta_ws_gb", "game working set"), ("gta_avail_gb", "system free"), ("gta_commit_pct * 0.32", "commit (% of ~32 GB, as GB)")], "decgbytes", 0, 8, minv=0)
ts("Paging (hard faults/s)", [("gta_pages_in_s", "pages read from disk/s")], "none", 8, 8, minv=0, desc="High values mean RAM is short and Windows is paging the game")
ts("GPU", [("gta_gpu_3d_pct", "3D engine %"), ("gta_gpu_copy_pct", "copy engine %"), ("gta_vram_gb * 6.25", "VRAM (% of 16 GB)")], "percent", 16, 8, minv=0)
y = 28
ts("CPU", [("gta_proc_cpu_pct", "game % of all cores"), ("gta_sys_cpu_pct", "system total %")], "percent", 0, 12, minv=0)
ts("Warnings & errors (cumulative this run)", [("gta_warn_total", "warnings"), ("gta_error_total", "errors")], "none", 12, 12, minv=0)
dash = {"uid": "gta6-victory", "title": "GTA6 - VIctory performance", "tags": ["pc", "games"], "timezone": "Asia/Kolkata",
        "refresh": "5s", "time": {"from": "now-30m", "to": "now"}, "schemaVersion": 39, "editable": True, "panels": panels,
        "description": "Pushed every ~2 s by gtamon (/docker/lab-ai/pc/gtamon). Source: dashboards-src/gta6_victory.py"}
out = pathlib.Path(__file__).resolve().parent.parent / "grafana/provisioning/dashboards/gta6-victory.json"
out.write_text(json.dumps(dash, indent=1)); print(out)
