"""Snapshot builder for the public landing page (the bare domain).

Runs in its own container with no published port. Once a minute it gathers numbers
from Prometheus, the media servers, the Pi-hole digest, Open-Meteo and the ECB rates,
and writes ONE file, /state/public/snapshot.json, which the web container serves
read-only. Visitors only ever read that file: nothing they send reaches this
process, so no request can make the lab call anything.

Every field in the snapshot is built explicitly below (never a pass-through of an
API response). It holds no LAN addresses, keys, container ids, titles or device
names - counts, percentages and public links only.

Standard library only. Config is the constants block; keys come from collector.env.
"""
import json
import os
import re
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))   # no DST in India, and Alpine has no tzdata
LAB = 'http://192.168.0.10'
PROM = f'{LAB}:9090'
STATE = os.environ.get('STATE_DIR', '/state')
PUBLIC = os.path.join(STATE, 'public')
DIGEST = os.environ.get('PIHOLE_DIGEST', '/pihole-digest/digest.db')
JELLYFIN_KEY = os.environ.get('JELLYFIN_KEY', '')
NAVIDROME_AUTH = {'u': os.environ.get('NAVIDROME_USER', ''), 't': os.environ.get('NAVIDROME_TOKEN', ''),
                  's': os.environ.get('NAVIDROME_SALT', ''), 'v': '1.16.1', 'c': 'landing', 'f': 'json'}
BANGALORE = (12.9716, 77.5946)
UA = 'landing-collector (+https://github.com/imad7x/homelab)'

# The public apps, for the status dots on the page's app tiles (the tiles themselves are
# static HTML). `check` is the LAN health URL and never leaves this process.
SERVICES = [
    # id, name, group, public url, description, LAN check, container
    ('jellyfin', 'Jellyfin', 'Media', 'https://jellyfin.[[private:DOMAIN]]', 'Movies & TV', f'{LAB}:8096/health', 'jellyfin'),
    ('jellyseerr', 'Jellyseerr', 'Media', 'https://jellyseerr.[[private:DOMAIN]]', 'Requests', f'{LAB}:5055/api/v1/status', 'jellyseerr'),
    ('navidrome', 'Navidrome', 'Media', 'https://navidrome.[[private:DOMAIN]]', 'Music', f'{LAB}:4533/ping', 'navidrome'),
    ('sonarr', 'Sonarr', 'Automation', 'https://sonarr.[[private:DOMAIN]]', 'TV library', f'{LAB}:8989/ping', 'sonarr'),
    ('radarr', 'Radarr', 'Automation', 'https://radarr.[[private:DOMAIN]]', 'Movie library', f'{LAB}:7878/ping', 'radarr'),
    ('bazarr', 'Bazarr', 'Automation', 'https://bazarr.[[private:DOMAIN]]', 'Subtitles', f'{LAB}:6767/', 'bazarr'),
    ('prowlarr', 'Prowlarr', 'Automation', 'https://prowlarr.[[private:DOMAIN]]', 'Indexers', f'{LAB}:9696/ping', 'prowlarr'),
    ('qbittorrent', 'qBittorrent', 'Downloads', 'https://qb.[[private:DOMAIN]]', 'Downloads', f'{LAB}:8080/', 'qbittorrent'),
    ('soulseek', 'Soulseek', 'Downloads', 'https://soulseek.[[private:DOMAIN]]', 'Music sharing', f'{LAB}:5030/health', 'slskd'),
    ('pihole', 'Pi-hole', 'Network', 'https://pihole.[[private:DOMAIN]]/admin', 'DNS & ad blocking', f'{LAB}:8081/admin/', 'pihole'),
    ('grafana', 'Grafana', 'Monitoring', 'https://grafana.[[private:DOMAIN]]/d/7xlab/my-lab', 'Dashboards', f'{LAB}:3001/api/health', 'grafana'),
    ('prometheus', 'Prometheus', 'Monitoring', 'https://p8s.[[private:DOMAIN]]', 'Metrics', f'{LAB}:9090/-/healthy', 'prometheus'),
    ('cadvisor', 'cAdvisor', 'Monitoring', 'https://cadvisor.[[private:DOMAIN]]', 'Container metrics', f'{LAB}:8082/healthz', 'cadvisor'),
    ('finance', 'Firefly III', 'Personal', 'https://finance.[[private:DOMAIN]]', 'Household ledger', f'{LAB}:3002/health', 'firefly'),
    ('gym', 'Health Tracker', 'Personal', 'https://gym.[[private:DOMAIN]]', 'Workouts & watch data', f'{LAB}:8086/api/ping', 'gym'),
]


def log(*a):
    print(datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def now_iso():
    return datetime.now(IST).isoformat(timespec='seconds')


def today():
    return datetime.now(IST).date()


def get(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(8_000_000))


def num(v, nd=None):
    """A finite float (rounded), or None - keeps NaN/Inf out of the JSON."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return round(f, nd) if nd is not None else f


# ------------------------------------------------------------------ Prometheus
def prom(q, at=None):
    p = {'query': q}
    if at is not None:
        p['time'] = f'{at:.3f}'
    d = get(f'{PROM}/api/v1/query?' + urllib.parse.urlencode(p))
    return d['data']['result']


def prom1(q, nd=None):
    r = prom(q)
    return num(r[0]['value'][1], nd) if r else None


def prom_range(q, hours=24, step=900, nd=2):
    end = time.time() // step * step
    p = {'query': q, 'start': end - hours * 3600, 'end': end, 'step': step}
    r = get(f'{PROM}/api/v1/query_range?' + urllib.parse.urlencode(p))['data']['result']
    if not r:
        return []
    return [[int(t), num(v, nd)] for t, v in r[0]['values']]


def running_containers():
    """Names cAdvisor saw in the last minute -> {name: True}. None if Prometheus is down."""
    try:
        return {x['metric']['name']: True
                for x in prom('time() - container_last_seen{name!=""} < 60')}
    except Exception as e:
        log('prometheus:', e)
        return None


def http_ok(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500       # a 401/403/404 still means the app answered
    except Exception:
        return False


# ------------------------------------------------------------------ tasks
def task_services():
    """Is each public app answering right now (from the LAN)? {id: bool}"""
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip([s[0] for s in SERVICES], ex.map(http_ok, [s[5] for s in SERVICES])))


def task_vitals():
    size = prom1('node_filesystem_size_bytes{mountpoint="/data"}')
    avail = prom1('node_filesystem_avail_bytes{mountpoint="/data"}')
    smart = prom('smartctl_device_smart_status')
    seen = running_containers() or {}
    # Percentages only: the page shows no core count, memory size or disk size.
    return {
        'cpu_pct': prom1('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))', 1),
        'mem_pct': prom1('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', 1),
        'temp_c': prom1('max(node_hwmon_temp_celsius{chip=~"platform_coretemp.*"})', 1),
        'pool_pct': None if not size or avail is None else round(100 * (size - avail) / size, 1),
        'containers': len(seen),
        'disks_ok': sum(1 for x in smart if x['value'][1] == '1'),
        'disks': len(smart),
    }


def task_trends():
    return {
        'step': 900,
        'cpu': prom_range('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[15m])))', nd=1),
        'mem': prom_range('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', nd=1),
        'temp': prom_range('max(avg_over_time(node_hwmon_temp_celsius{chip=~"platform_coretemp.*"}[15m]))', nd=1),
    }


def navidrome(method, **params):
    q = urllib.parse.urlencode({**NAVIDROME_AUTH, **params})
    r = get(f'{LAB}:4533/rest/{method}?{q}')['subsonic-response']
    if r.get('status') != 'ok':
        raise RuntimeError(f'navidrome {method}: {r.get("error", {}).get("message")}')
    return r


def task_media():
    j = get(f'{LAB}:8096/Items/Counts', headers={'Authorization': f'MediaBrowser Token="{JELLYFIN_KEY}"'})
    songs = navidrome('getScanStatus')['scanStatus'].get('count')
    artists = sum(len(i.get('artist', [])) for i in navidrome('getArtists')['artists'].get('index', []))
    albums, offset = 0, 0
    while True:
        page = navidrome('getAlbumList2', type='alphabeticalByName', size=500, offset=offset)
        n = len(page.get('albumList2', {}).get('album', []))
        albums, offset = albums + n, offset + n
        if n < 500:
            break
    return {'movies': j.get('MovieCount'), 'shows': j.get('SeriesCount'), 'episodes': j.get('EpisodeCount'),
            'collections': j.get('BoxSetCount'), 'songs': songs, 'albums': albums, 'artists': artists}


def task_streaming():
    s = get(f'{LAB}:8096/Sessions?activeWithinSeconds=120',
            headers={'Authorization': f'MediaBrowser Token="{JELLYFIN_KEY}"'})
    entries = navidrome('getNowPlaying').get('nowPlaying', {}).get('entry', [])
    return {'video': sum(1 for x in s if x.get('NowPlayingItem')),
            'music': sum(1 for x in entries if (x.get('minutesAgo') or 0) <= 10)}


def task_pihole():
    try:
        c = sqlite3.connect(f'file:{DIGEST}?mode=ro', uri=True)
        c.execute('SELECT 1 FROM days LIMIT 1')
    except sqlite3.OperationalError:
        c = sqlite3.connect(f'file:{DIGEST}?immutable=1', uri=True)
    try:
        rows = c.execute('SELECT day, queries, blocked FROM days WHERE day < ? ORDER BY day DESC LIMIT 30',
                         (today().isoformat(),)).fetchall()
        meta = dict(c.execute('SELECT key, value FROM meta').fetchall())
    finally:
        c.close()
    return {'days': [{'day': d, 'queries': int(q or 0), 'blocked': int(b or 0)} for d, q, b in reversed(rows)],
            'blocklist': int(meta.get('gravity_domains') or 0) or None}


WMO = {0: 'Clear', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Fog',
       51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle', 56: 'Freezing drizzle', 57: 'Freezing drizzle',
       61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain',
       71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains', 80: 'Light showers',
       81: 'Showers', 82: 'Heavy showers', 85: 'Snow showers', 86: 'Snow showers',
       95: 'Thunderstorm', 96: 'Thunderstorm, hail', 99: 'Thunderstorm, hail'}


def task_weather():
    """Now, today's high/low, and the chance of rain hour by hour for the next 24 h."""
    lat, lon = BANGALORE
    f = get('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode({
        'latitude': lat, 'longitude': lon, 'timezone': 'Asia/Kolkata',
        'current': 'temperature_2m,apparent_temperature,weather_code,is_day',
        'hourly': 'precipitation_probability,precipitation', 'forecast_hours': 24,
        'daily': 'temperature_2m_max,temperature_2m_min', 'forecast_days': 1}))
    cur, d, h = f['current'], f['daily'], f['hourly']
    code = int(cur.get('weather_code') or 0)
    return {
        'place': 'Bangalore', 'code': code, 'label': WMO.get(code, 'Weather'), 'day': bool(cur.get('is_day')),
        'temp': num(cur.get('temperature_2m'), 1), 'feels': num(cur.get('apparent_temperature'), 1),
        'max': num(d['temperature_2m_max'][0], 0), 'min': num(d['temperature_2m_min'][0], 0),
        # [HH:MM, chance of rain %, mm]
        'rain': [[str(h['time'][i])[-5:], num(h['precipitation_probability'][i], 0), num(h['precipitation'][i], 1)]
                 for i in range(len(h['time']))],
    }


def task_fx():
    start = (today() - timedelta(days=45)).isoformat()
    d = get(f'https://api.frankfurter.dev/v1/{start}..?base=USD&symbols=INR')
    series = [[day, num(v.get('INR'), 4)] for day, v in sorted(d.get('rates', {}).items())
              if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)]
    if not series:
        raise RuntimeError('no rates')
    return {'pair': 'USD/INR', 'rate': series[-1][1], 'date': series[-1][0], 'series': series[-30:],
            'source': 'European Central Bank reference rate'}


TASKS = [  # name, seconds between runs, function
    ('services', 60, task_services), ('vitals', 60, task_vitals), ('streaming', 60, task_streaming),
    ('trends', 300, task_trends), ('media', 900, task_media), ('weather', 900, task_weather),
    ('pihole', 1800, task_pihole), ('fx', 3600, task_fx),
]


# ------------------------------------------------------------------ snapshot
def build(cache):
    now_up = (cache.get('services') or {}).get('data') or {}
    services = [{'id': sid, 'name': name, 'up': now_up.get(sid)} for sid, name, *_ in SERVICES]
    snap = {'v': 2, 'generated': now_iso(), 'services': services}
    for name, _every, _fn in TASKS:
        if name != 'services' and cache.get(name):
            snap[name] = cache[name]
    return snap


def write(snap):
    os.makedirs(PUBLIC, exist_ok=True)
    tmp = os.path.join(PUBLIC, '.snapshot.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(snap, f, separators=(',', ':'), allow_nan=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(PUBLIC, 'snapshot.json'))


def main():
    os.makedirs(PUBLIC, exist_ok=True)
    cache = {}
    try:   # keep the slow sources from the last run until they refresh
        with open(os.path.join(PUBLIC, 'snapshot.json')) as f:
            old = json.load(f)
        cache = {k: old[k] for k, *_ in TASKS if isinstance(old.get(k), dict) and 'at' in old[k]}
    except Exception:
        pass
    due = {name: 0 for name, *_ in TASKS}
    failing = set()
    log('collector started')
    while True:
        start = time.time()
        for name, every, fn in TASKS:
            if start < due[name]:
                continue
            try:
                cache[name] = {'at': now_iso(), 'data': fn()} if name == 'services' else {'at': now_iso(), **fn()}
                due[name] = start + every
                if name in failing:
                    failing.discard(name)
                    log(name, 'recovered')
            except Exception as e:
                due[name] = start + min(every, 300)    # retry failing sources within 5 minutes
                if name not in failing:
                    log(name, 'failed:', repr(e))
                    if os.environ.get('DEBUG'):
                        traceback.print_exc()
                failing.add(name)
        try:
            write(build(cache))
        except Exception as e:
            log('write failed:', repr(e))
        if '--once' in sys.argv:
            return
        time.sleep(max(5, 60 - (time.time() - start)))


if __name__ == '__main__':
    main()
