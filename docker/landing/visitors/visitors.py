#!/usr/bin/env python3
"""Visitors to the public landing page: who came, from where, on what.

nginx (../nginx/default.conf) writes one JSON line per page request that came in
through Cloudflare to ../logs/visits-YYYY-MM.jsonl, and each email left in the footer
to ../logs/requests-YYYY-MM.jsonl. This file reads them. It is the `visitors` command
and the engine behind the LAN dashboard (server.py, port 3011).

  visitors             latest visits, newest first (bots and scanners hidden)
  visitors -n 100      more of them
  visitors devices     one line per device, with visit counts
  visitors requests    emails left in the footer's "need access?" box
  visitors report      the latest weekly report (Monday to Sunday)
  visitors --all       include bots, link previews, scanners and 404 probes

A device is a browser: the random `vid` cookie the page hands out or, for a browser that
never sends it back (a one-off visit, a private window, a bot), its IP + user agent.
Names and "this is me" marks live in ../visitors-state/labels.json; the blocklist in
../visitors-state/blocklist.json, from which ../blocklist/*.conf is written for nginx.
Location is Cloudflare's estimate for the IP address (city level), never the device's
real position.
"""
import argparse, glob, hashlib, ipaddress, json, os, re, sys, urllib.parse
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # / in the container
LOGS = os.path.join(BASE, 'logs')
STATE = os.path.join(BASE, 'visitors-state')
REPORTS = os.path.join(STATE, 'reports')
BLOCKLIST_DIR = os.path.join(BASE, 'blocklist')
DASHBOARD = 'http://192.168.0.10:3011'
IST = timezone(timedelta(hours=5, minutes=30))
REPEAT = 600   # seconds: reloads (and the browser's client-hint retry) within 10 min are one visit
VID = re.compile(r'^[0-9a-f]{32}$')
KEY = re.compile(r'^(c:[0-9a-f]{32}|u:[0-9a-f]{16})$')
EMAIL = re.compile(r'^[^\s@]{1,64}@[^\s@]{1,190}\.[A-Za-z]{2,24}$')

# ---------------------------------------------------------------- user agents
PREVIEWS = [('whatsapp', 'WhatsApp'), ('telegrambot', 'Telegram'), ('slack', 'Slack'),
    ('discordbot', 'Discord'), ('facebookexternalhit', 'Facebook'), ('twitterbot', 'X'),
    ('linkedinbot', 'LinkedIn'), ('skypeuripreview', 'Skype'), ('applebot', 'Apple'),
    ('googleother', 'Google')]
BOT = re.compile(r'bot|crawl|spider|slurp|scan|curl|wget|python|go-http|java/|okhttp|httpclient|'
    r'headless|zgrab|masscan|nmap|nikto|censys|expanse|libwww|axios|node-fetch|preview|fetch|monitor|'
    r'selftest', re.I)
BROWSERS = [(r'EdgA?/(\d+)|EdgiOS/(\d+)', 'Edge'), (r'OPR/(\d+)|OPiOS/(\d+)', 'Opera'),
    (r'SamsungBrowser/(\d+)', 'Samsung Internet'), (r'Firefox/(\d+)|FxiOS/(\d+)', 'Firefox'),
    (r'CriOS/(\d+)', 'Chrome'), (r'Chrome/(\d+)', 'Chrome'), (r'Version/(\d+)[\d.]* (?:Mobile/\w+ )?Safari/', 'Safari')]
# Apple's model identifiers, which in-app browsers (Instagram, Facebook) put in their
# user agent; Safari itself never says which iPhone it is.
IPHONES = {
    'iPhone12,1': 'iPhone 11', 'iPhone12,3': 'iPhone 11 Pro', 'iPhone12,5': 'iPhone 11 Pro Max',
    'iPhone12,8': 'iPhone SE (2nd gen)', 'iPhone13,1': 'iPhone 12 mini', 'iPhone13,2': 'iPhone 12',
    'iPhone13,3': 'iPhone 12 Pro', 'iPhone13,4': 'iPhone 12 Pro Max', 'iPhone14,4': 'iPhone 13 mini',
    'iPhone14,5': 'iPhone 13', 'iPhone14,2': 'iPhone 13 Pro', 'iPhone14,3': 'iPhone 13 Pro Max',
    'iPhone14,6': 'iPhone SE (3rd gen)', 'iPhone14,7': 'iPhone 14', 'iPhone14,8': 'iPhone 14 Plus',
    'iPhone15,2': 'iPhone 14 Pro', 'iPhone15,3': 'iPhone 14 Pro Max', 'iPhone15,4': 'iPhone 15',
    'iPhone15,5': 'iPhone 15 Plus', 'iPhone16,1': 'iPhone 15 Pro', 'iPhone16,2': 'iPhone 15 Pro Max',
    'iPhone17,1': 'iPhone 16 Pro', 'iPhone17,2': 'iPhone 16 Pro Max', 'iPhone17,3': 'iPhone 16',
    'iPhone17,4': 'iPhone 16 Plus', 'iPhone17,5': 'iPhone 16e', 'iPhone18,1': 'iPhone 17 Pro',
    'iPhone18,2': 'iPhone 17 Pro Max', 'iPhone18,3': 'iPhone 17', 'iPhone18,4': 'iPhone Air',
}
IN_APP = [(r'Instagram', 'Instagram'), (r'FBAN|FBAV', 'Facebook'), (r'LinkedInApp', 'LinkedIn'),
    (r'Snapchat', 'Snapchat'), (r'\bLine/', 'LINE'), (r'GSA/', 'Google app')]


def unquote(v):
    return (v or '').strip().strip('"')


def bot_name(ua):
    """None for a person's browser, else a short label for the bot."""
    low = ua.lower()
    if not ua.strip():
        return 'bot (no user agent)'
    for key, name in PREVIEWS:
        if key in low:
            return f'{name} link preview'
    if not BOT.search(ua):
        return None
    name = next((tok for tok in re.findall(r'[A-Za-z][\w.-]*', ua) if BOT.search(tok)), '')
    return f'bot ({name})' if name else 'bot'


def device(r):
    """('Android 16 · V2413 · Chrome 154', is_bot) from the user agent and client hints."""
    ua = r.get('ua') or ''
    bot = bot_name(ua)
    if bot:
        return bot, True
    model, pver = unquote(r.get('model')), unquote(r.get('platform_version'))
    if 'iPhone' in ua or 'iPad' in ua:
        # In-app browsers add "(iPhone17,2; iOS 26_1; ...)"; Safari freezes its OS version.
        m = re.search(r'\((i(?:Phone|Pad)\d+,\d+); iOS (\d+)[_.](\d+)', ua)
        system = 'iPhone' if 'iPhone' in ua else 'iPad'
        if m:
            system = f"{IPHONES.get(m.group(1), m.group(1))} · iOS {m.group(2)}.{m.group(3)}"
    elif 'Android' in ua:
        m = re.search(r'Android ([\d.]+); ([^;)]+?)(?: Build/[^;)]*)?\)', ua)
        ua_version, ua_model = (m.group(1).split('.')[0], m.group(2).strip()) if m else ('', '')
        reduced = ua_model in ('K', 'wv')  # Chrome's frozen "Android 10; K"
        model = model or ('' if reduced else ua_model)
        version = pver.split('.')[0] or ('' if reduced else ua_version)
        system = ' · '.join(x for x in (f'Android {version}'.strip(), model) if x)
    elif 'Windows NT' in ua:
        major = int(pver.split('.')[0]) if pver.split('.')[0].isdigit() else 0
        system = 'Windows 11' if major >= 13 else 'Windows'
    elif 'Macintosh' in ua or 'Mac OS X' in ua:
        system = 'Mac'
    elif 'CrOS' in ua:
        system = 'Chromebook'
    elif 'Linux' in ua:
        system = 'Linux'
    else:
        system = 'Unknown device'
    for pattern, name in IN_APP:
        if re.search(pattern, ua):
            return f'{system} · {name} app', False
    browser = 'Browser'
    for pattern, name in BROWSERS:
        m = re.search(pattern, ua)
        if m:
            version = next((g for g in m.groups() if g), '')
            if name == 'Chrome' and 'Brave' in (r.get('brands') or ''):
                name = 'Brave'
            browser = f'{name} {version}'.strip()
            break
    return f'{system} · {browser}', False


def place(r):
    city, cc = (r.get('city') or '').strip(), (r.get('country') or '').strip()
    if cc in ('', 'XX'):
        cc = '??'
    if cc == 'T1':
        return 'Tor network'
    return f'{city}, {cc}' if city else cc


def area(r):
    """Longer location for details: city, postcode, region, country + map coordinates."""
    if (r.get('country') or '') == 'T1':
        return {'text': 'Tor network (location hidden)'}
    city = ' '.join(x for x in ((r.get('city') or '').strip(), (r.get('postal') or '').strip()) if x)
    text = ', '.join(x for x in (city, (r.get('region') or '').strip(), (r.get('country') or '').strip()) if x)
    out = {'text': text or 'Unknown'}
    try:
        lat, lon = float(r.get('lat')), float(r.get('lon'))
        out.update(lat=lat, lon=lon,
                   map=f'https://www.openstreetmap.org/?mlat={lat:.4f}&mlon={lon:.4f}#map=11/{lat:.4f}/{lon:.4f}')
    except (TypeError, ValueError):
        pass
    return out


SITE = '[[private:DOMAIN]]'


def source(r):
    """Where the visit came from: Instagram's bio link, a referring site, or direct."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(r.get('path') or '').query)
    ua, ref = r.get('ua') or '', r.get('ref') or ''
    src = q.get('utm_source', [''])[0].lower()
    if src in ('ig', 'instagram') or 'Instagram' in ua:
        return 'Instagram bio link' if q.get('utm_content', [''])[0] == 'link_in_bio' else 'Instagram'
    if 'fbclid' in q or re.search(r'FBAN|FBAV', ua):
        return 'Facebook'
    host = (urllib.parse.urlsplit(ref).hostname or '').removeprefix('www.')
    if host and not host.endswith(SITE):
        return host
    return src.capitalize() if src else ('Direct' if not ref else 'This site')


def language(r):
    """First language the browser asks for, or the locale an in-app browser reports."""
    m = re.search(r'; ([a-z]{2}[_-][A-Z]{2}); [a-z]{2}', r.get('ua') or '')
    first = (r.get('lang') or '').split(',')[0].split(';')[0].strip()
    return first or (m.group(1).replace('_', '-') if m else '')


def colo(r):
    """Cloudflare data centre that served the visit (from the CF-Ray id, e.g. BLR, BOM)."""
    m = re.search(r'-([A-Z]{3})$', r.get('ray') or '')
    return m.group(1) if m else ''


# ---------------------------------------------------------------- state files
def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def labels():
    return read_json(os.path.join(STATE, 'labels.json'), {}).get('devices', {})


def set_label(key, name=None, mine=None):
    if not KEY.match(key or ''):
        raise ValueError('unknown device')
    data = read_json(os.path.join(STATE, 'labels.json'), {})
    entry = data.setdefault('devices', {}).setdefault(key, {})
    if name is not None:
        name = ''.join(c for c in str(name) if c.isprintable()).strip()[:40]
        if name:
            entry['name'] = name
        else:
            entry.pop('name', None)
    if mine is not None:
        entry['mine'] = bool(mine)
    entry['at'] = datetime.now(IST).isoformat(timespec='seconds')
    write_json(os.path.join(STATE, 'labels.json'), data)


def blocklist():
    return read_json(os.path.join(STATE, 'blocklist.json'), {}).get('entries', [])


def parse_net(value):
    """An IP or range nginx's geo block can take; refuses anything wider than a /16 (v6: /32)."""
    net = ipaddress.ip_network(str(value).strip(), strict=False)
    if net.prefixlen < (16 if net.version == 4 else 32):
        raise ValueError(f'{net} is too wide a range to block')
    return str(net)


def write_blocklist(entries):
    """Save the list and regenerate nginx's include files from validated values only."""
    write_json(os.path.join(STATE, 'blocklist.json'), {'entries': entries})
    head = '# Written by the visitors dashboard - edits here are overwritten.\n'
    ips = [f'{parse_net(e["value"])} 1;\n' for e in entries if e['kind'] == 'ip']
    vids = [f'{e["value"]} 1;\n' for e in entries if e['kind'] == 'device' and VID.match(e['value'])]
    for name, lines in (('ips.conf', ips), ('devices.conf', vids)):
        path = os.path.join(BLOCKLIST_DIR, name)
        with open(f'{path}.tmp', 'w') as fh:
            fh.write(head + ''.join(sorted(set(lines))))
        os.chmod(f'{path}.tmp', 0o644)
        os.replace(f'{path}.tmp', path)


def is_blocked(entries, vid=None, ip=None):
    for e in entries:
        if e['kind'] == 'device' and vid and e['value'] == vid:
            return True
        if e['kind'] == 'ip' and ip:
            try:
                if ipaddress.ip_address(ip) in ipaddress.ip_network(e['value']):
                    return True
            except ValueError:
                pass
    return False


# ---------------------------------------------------------------- reading the logs
def load(kind='visits'):
    rows = []
    for path in sorted(glob.glob(os.path.join(LOGS, f'{kind}-*.jsonl'))):
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    r['when'] = datetime.fromisoformat(r['t']).astimezone(IST)
                except (ValueError, KeyError, TypeError):
                    continue
                r['ts'] = r['when'].timestamp()
                r['device'], r['bot'] = device(r)
                r['place'] = place(r)
                path_only = (r.get('path') or '/').split('?')[0]
                r['page'] = path_only in ('/', '/index.html') and str(r.get('status')) in ('200', '304')
                r['blocked'] = str(r.get('blocked')) == '1'
                r['email'] = urllib.parse.parse_qs(r.get('body') or '').get('email', [''])[0].strip()
                r['source'], r['language'], r['colo'] = source(r), language(r), colo(r)
                rows.append(r)
    rows.sort(key=lambda r: r['ts'])
    assign_keys(rows)
    return rows


def assign_keys(rows):
    """Cookie id once the browser has sent it back at least once (a reload, the page's
    refresh, an event, an email), else IP + user agent."""
    returning = {r.get('vid') for r in rows if str(r.get('vid_new')) == '0' and VID.match(r.get('vid') or '')}
    returning |= {r['vid'] for kind in ('pings', 'events', 'requests') for r in side_log(kind) if r['vid']}
    pairs = {}
    for r in rows:
        r['ukey'] = 'u:' + hashlib.sha1(f"{r.get('ip')}|{r.get('ua')}".encode()).hexdigest()[:16]
        r['key'] = f"c:{r['vid']}" if r.get('vid') in returning else None
        if r['key']:
            pairs.setdefault((r.get('ip'), r.get('ua')), []).append((r['ts'], r['key']))
    for r in rows:
        if not r['key']:
            # The first load of a browser whose id arrived only with the retry, or a row
            # from before the cookie existed (no vid at all): same IP + user agent string.
            same = pairs.get((r.get('ip'), r.get('ua')), [])
            near = [k for ts, k in same if not r.get('vid') or abs(ts - r['ts']) < REPEAT]
            r['key'] = near[0] if near else r['ukey']


GAP = 180  # seconds without a snapshot refresh before an open page counts as closed
SESSION_GAP = 1800  # seconds of nothing before the next activity starts a new session


def side_log(kind):
    """Rows of pings / events / requests with their device keys; bots left out. 'vid' is
    set only when the browser sent its cookie (so it identifies the device)."""
    out = []
    for path in sorted(glob.glob(os.path.join(LOGS, f'{kind}-*.jsonl'))):
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    r['when'] = datetime.fromisoformat(r['t']).astimezone(IST)
                except (ValueError, KeyError, TypeError):
                    continue
                if bot_name(r.get('ua') or ''):
                    continue
                r['ts'] = r['when'].timestamp()
                r['ukey'] = 'u:' + hashlib.sha1(f"{r.get('ip')}|{r.get('ua')}".encode()).hexdigest()[:16]
                vid = r.get('vid') or ''
                r['vid'] = vid if str(r.get('vid_new')) == '0' and VID.match(vid) else ''
                r['key'] = f"c:{r['vid']}" if r['vid'] else r['ukey']
                out.append(r)
    out.sort(key=lambda r: r['ts'])
    return out


def pings():
    """(key, ukey, ts) for each once-a-minute refresh of an open page."""
    return [(r['key'], r['ukey'], r['ts']) for r in side_log('pings')]


def analyse(start=None, end=None, rows=None):
    """Everything the dashboard, the report and the command show, for [start, end)."""
    rows = load() if rows is None else rows
    names, blocks = labels(), blocklist()
    refresh, beats = {}, pings()
    measured_from = beats[0][2] - 90 if beats else float('inf')   # refreshes weren't logged before this
    for key, ukey, ts in beats:
        refresh.setdefault(key, []).append(ts)
        if ukey != key:
            refresh.setdefault(ukey, []).append(ts)
    inside = lambda r: (start is None or r['when'] >= start) and (end is None or r['when'] < end)

    devices = {}
    for r in rows:
        if r['bot']:
            continue
        d = devices.get(r['key'])
        if d is None:
            d = devices[r['key']] = {'key': r['key'], 'aliases': set(), 'first': r['when'], 'visits': 0,
                                     'visits_all': 0, 'attempts': 0, 'ips': {}, 'places': {}}
        d['aliases'].add(r['ukey'])
        d['last'] = r['when']
        if 'auto' not in d or unquote(r.get('model')) or not r['page']:
            d['auto'] = r['device']
        d['ip'], d['place'], d['area'] = r.get('ip', ''), r['place'], area(r)
        d['source'], d['language'], d['colo'] = r['source'], r['language'] or d.get('language', ''), r['colo']
        if inside(r):
            d['ips'][r.get('ip', '')] = r['when']
            d['places'][r['place']] = r['when']
            if r['blocked']:
                d['attempts'] += 1

    # One visit = a page load and its reloads within REPEAT seconds, per device.
    visits, last = [], {}
    for r in rows:
        if r['bot'] or not r['page'] or r['blocked']:
            continue
        prev = last.get(r['key'])
        if prev and r['ts'] - prev[0] < REPEAT:
            prev[0] = r['ts']
            if unquote(r.get('model')) and not unquote(prev[1].get('model')):
                prev[1]['device'], prev[1]['model'] = r['device'], r.get('model')
            continue
        v = dict(r)
        last[r['key']] = [r['ts'], v]
        visits.append(v)
        # Open for: follow the page's minute-by-minute refreshes until they stop.
        until = r['ts']
        for ts in sorted(set(refresh.get(r['key'], []) + refresh.get(r['ukey'], []))):
            if ts < r['ts']:
                continue
            if ts - until > GAP:
                break
            until = ts
        v['open_s'] = int(until - r['ts']) if r['ts'] >= measured_from else None
        devices[r['key']]['visits_all'] += 1
        if inside(r):
            devices[r['key']]['visits'] += 1
    visits = [v for v in visits if inside(v)]

    for d in devices.values():
        seen_ts = [ts for k in {d['key'], *d['aliases']} for ts in refresh.get(k, [])]
        latest = max(seen_ts, default=0)
        d['active'] = max(d['last'], datetime.fromtimestamp(latest, IST)) if latest else d['last']
    mine_ips = set()
    for d in devices.values():
        label = names.get(d['key']) or next((names[a] for a in sorted(d['aliases']) if a in names), {})
        d['name'] = label.get('name') or d['auto']
        d['mine'] = bool(label.get('mine'))
        d['cookie'] = d['key'].startswith('c:')
        d['blocked'] = is_blocked(blocks, d['key'][2:] if d['cookie'] else None, d['ip'])
        if d['mine']:
            mine_ips.update(d['ips'])
    for d in devices.values():
        d['your_network'] = not d['mine'] and bool(mine_ips & set(d['ips']))
    for v in visits:
        d = devices[v['key']]
        v['name'], v['mine'] = d['name'], d['mine']

    shown = [d for d in devices.values() if d['visits'] or d['attempts']]
    shown.sort(key=lambda d: d['last'], reverse=True)
    others = [r for r in rows if inside(r) and not r['blocked'] and (r['bot'] or not r['page'])]
    attempts = [r for r in rows if inside(r) and r['blocked']]
    return {'devices': shown, 'visits': visits, 'others': others, 'attempts': attempts,
            'all_devices': devices, 'blocklist': blocks}


SECTIONS = {'apps': 'Looked at the apps', 'vitals': 'Scrolled to server vitals',
            'around': 'Scrolled to weather, media and rates', 'dns': 'Scrolled to DNS filtering',
            'footer': 'Reached the bottom of the page'}
TABLES = {'fx': 'USD to INR', 'dns': 'DNS filtering'}


def app_names():
    """App tile ids to names, read from the public page itself."""
    try:
        with open(os.path.join(BASE, 'site', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
    except OSError:
        return {}
    names = dict(re.findall(r'data-id="([\w-]+)"[^>]*>.*?<span class="app-name">([^<]+)</span>', html))
    names['source'] = 'the GitHub source link'
    return names


def activity(key):
    """Everything one device did, as sessions (newest first) of timestamped steps."""
    rows = load()
    a = analyse(rows=rows)
    d = a['all_devices'].get(key)
    if not d:
        raise ValueError('unknown device')
    keys = {key} | d['aliases']
    ours = lambda r: r['key'] == key or (r['key'].startswith('u:') and r['ukey'] in keys)
    apps = app_names()
    steps = []   # (ts, end_ts, kind, text, row)

    last_load = 0
    for r in rows:
        if r['key'] != key:
            continue
        if r['page'] and not r['blocked']:
            if r['ts'] - last_load < 5:   # the browser's client-hint retry of the same load
                continue
            last_load = r['ts']
        if r['blocked']:
            steps.append((r['ts'], None, 'blocked', f"Turned away by the blocklist ({r.get('path') or '/'})", r))
        elif r['page']:
            steps.append((r['ts'], None, 'load', '', r))
        else:
            steps.append((r['ts'], None, 'probe', f"Asked for {(r.get('path') or '')[:80]} ({r.get('status')})", r))
    for e in side_log('events'):
        if not ours(e):
            continue
        act, target = e.get('a'), e.get('target') or ''
        text = {'open': f"Tapped {apps.get(target, target)}",
                'view': SECTIONS.get(target, f'Scrolled to {target}'),
                'hide': 'Left the page (switched app or tab)', 'show': 'Came back to the page',
                'theme': f'Switched to the {target} theme',
                'table': f"Opened the {TABLES.get(target, target)} table"}.get(act)
        if text:
            steps.append((e['ts'], None, act, text, e))
    for q in requests():
        if ours(q):
            steps.append((q['ts'], None, 'email', f"Left their email: {q['email'][:120]}" if q['valid']
                          else 'Sent the email box something that is not an email', q))
    # Runs of the page's once-a-minute refreshes; a run under a minute is just the page's
    # own first load of its data.
    runs = []
    for p in side_log('pings'):
        if not ours(p):
            continue
        if runs and p['ts'] - runs[-1][1] <= GAP:
            runs[-1][1], runs[-1][2] = p['ts'], runs[-1][2] + 1
        else:
            runs.append([p['ts'], p['ts'], 1])
    steps += [(a, b, 'alive', n, None) for a, b, n in runs if b - a >= 60]   # text slot carries the count
    steps.sort(key=lambda x: x[0])

    sessions, cur = [], None
    for ts, end, kind, text, r in steps:
        if cur is None or ts - cur['end_ts'] > SESSION_GAP:
            cur = {'start_ts': ts, 'end_ts': ts, 'items': [], 'ips': {}, 'places': {}, 'source': '', 'loads': 0}
            sessions.append(cur)
        cur['end_ts'] = max(cur['end_ts'], end or ts)
        if r is not None and r.get('ip'):
            cur['ips'][r['ip']] = 1
        if kind == 'load':
            text = 'Opened the page' if not cur['loads'] else 'Reloaded the page'
            if not cur['loads'] and r['source'] not in ('Direct', 'This site'):
                text += f" from {r['source']}"
            cur['source'] = cur['source'] or r['source']
            cur['places'][r['place']] = 1
            cur['loads'] += 1
        elif kind == 'alive':
            n = text
            text = f'Kept the page open ({n} refreshes, one a minute)'
        cur['items'].append({'t': datetime.fromtimestamp(ts, IST).isoformat(timespec='seconds'),
                             'end': datetime.fromtimestamp(end, IST).isoformat(timespec='seconds') if end else None,
                             'kind': kind, 'text': text,
                             'where': r['place'] if r is not None and kind == 'load' else None,
                             'ip': r.get('ip') if r is not None and kind == 'load' else None})
    out = []
    for c in reversed(sessions[-50:]):
        out.append({'start': datetime.fromtimestamp(c['start_ts'], IST).isoformat(timespec='seconds'),
                    'end': datetime.fromtimestamp(c['end_ts'], IST).isoformat(timespec='seconds'),
                    'duration_s': int(c['end_ts'] - c['start_ts']), 'ips': list(c['ips']),
                    'places': list(c['places']), 'source': c['source'], 'items': c['items']})
    return d, out


def requests(start=None, end=None):
    out = []
    for r in load('requests'):
        if str(r.get('status')) != '204' or (start and r['when'] < start) or (end and r['when'] >= end):
            continue
        r['valid'] = bool(EMAIL.match(r['email']))
        out.append(r)
    return out


# ---------------------------------------------------------------- weekly reports
def week_start(t):
    t = t.astimezone(IST)
    return datetime(t.year, t.month, t.day, tzinfo=IST) - timedelta(days=t.weekday())


def week_label(start):
    end = start + timedelta(days=6)
    left = f'{start.day} {start:%b}' if start.month != end.month else f'{start.day}'
    return f'{left}–{end.day} {end:%b %Y}'


def make_report(start, rows=None):
    end = start + timedelta(days=7)
    a = analyse(start, end, rows)
    people = a['devices']
    unknown = [d for d in people if not d['mine'] and d['visits']]
    return {
        'week': start.date().isoformat(), 'label': week_label(start),
        'generated': datetime.now(IST).isoformat(timespec='seconds'),
        'visits': len(a['visits']), 'devices': sum(1 for d in people if d['visits']),
        'mine_visits': sum(1 for v in a['visits'] if v['mine']),
        'unknown_visits': sum(1 for v in a['visits'] if not v['mine']),
        'unknown': [{'name': d['name'], 'device': d['auto'], 'visits': d['visits'],
                     'first': min(d['ips'].values()).isoformat(timespec='minutes'),
                     'last': max(d['ips'].values()).isoformat(timespec='minutes'),
                     'ips': list(d['ips']), 'places': list(d['places']), 'area': d['area'],
                     'your_network': d['your_network'], 'blocked': d['blocked']} for d in unknown],
        'mine': [{'name': d['name'], 'visits': d['visits']} for d in people if d['mine'] and d['visits']],
        'blocked_attempts': len(a['attempts']),
        'bots': sum(1 for r in a['others'] if r['bot']),
        'probes': sum(1 for r in a['others'] if not r['bot']),
        'requests': [{'when': r['when'].isoformat(timespec='minutes'), 'email': r['email'],
                      'place': r['place'], 'device': r['device'], 'ip': r.get('ip', '')}
                     for r in requests(start, end) if r['valid']],
    }


def ensure_reports(rows=None):
    """Write a report for every finished week since logging began (up to 12 back)."""
    rows = load() if rows is None else rows
    if not rows:
        return []
    done = week_start(datetime.now(IST))
    week = max(week_start(rows[0]['when']), done - timedelta(weeks=12))
    made = []
    while week < done:
        path = os.path.join(REPORTS, f'{week.date().isoformat()}.json')
        if not os.path.exists(path):
            write_json(path, make_report(week, rows))
            made.append(path)
        week += timedelta(weeks=1)
    return made


def reports():
    out = [read_json(p, None) for p in sorted(glob.glob(os.path.join(REPORTS, '*.json')), reverse=True)]
    return [r for r in out if r]


# ---------------------------------------------------------------- "seen" (for the login brief)
def seen():
    return read_json(os.path.join(STATE, 'seen.json'), {})


def mark_seen():
    latest = reports()
    write_json(os.path.join(STATE, 'seen.json'), {
        'opened': datetime.now(IST).isoformat(timespec='seconds'),
        'report': latest[0]['week'] if latest else seen().get('report')})


def brief():
    """One line for lab-brief: 'ok|warn<TAB>text'."""
    rows = load()
    ensure_reports(rows)
    s = seen()
    since = datetime.fromisoformat(s['opened']) if s.get('opened') else datetime.now(IST) - timedelta(days=7)
    a = analyse(since, None, rows)
    unknown = [d for d in a['devices'] if not d['mine'] and d['visits']]
    parts, level = [], 'ok'
    if unknown:
        level = 'warn'
        n = sum(d['visits'] for d in unknown)
        parts.append(f"{len(unknown)} unknown device{'s' * (len(unknown) != 1)} visited since you last looked "
                     f"({n} visit{'s' * (n != 1)}, latest from {unknown[0]['place']})")
    else:
        parts.append('no unknown devices since you last looked')
    latest = reports()
    if latest and latest[0]['week'] != s.get('report'):
        r = latest[0]
        parts.append(f"weekly report {r['label']} ready ({len(r['unknown'])} unknown)")
    if level == 'warn' or len(parts) > 1:
        parts.append(f"`visitors` or {DASHBOARD.split('//')[1]}")
    return f"{level}\t{' · '.join(parts)}"


# ---------------------------------------------------------------- command line
def clean(v):
    """Logged values come from strangers: drop anything that could drive the terminal."""
    return ''.join(c if c.isprintable() else '?' for c in str(v))


def table(headers, lines):
    if not lines:
        return
    lines = [[clean(x) for x in line] for line in lines]
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *lines)]
    bold, plain = ('\033[1m', '\033[0m') if sys.stdout.isatty() else ('', '')
    fmt = '  '.join(f'{{:<{w}}}' for w in widths)
    print(bold + fmt.format(*headers).rstrip() + plain)
    for line in lines:
        print(fmt.format(*line).rstrip())


def stamp(t):
    return t.strftime('%a %d %b  %H:%M')


def duration(sec):
    if sec is None:
        return '—'
    if sec < 60:
        return '<1 min'
    return f'{sec // 60} min' if sec < 3600 else f'{sec // 3600} h {sec % 3600 // 60} min'


def print_report(r):
    print(f"Week {r['label']}: {r['visits']} visit(s) from {r['devices']} device(s); "
          f"{r['mine_visits']} yours, {r['unknown_visits']} unknown.")
    if r['unknown']:
        print()
        table(['UNKNOWN DEVICE', 'VISITS', 'WHERE', 'IPS', 'LAST SEEN'],
              [[d['name'], d['visits'], ', '.join(d['places']), ', '.join(d['ips'][:3]),
                stamp(datetime.fromisoformat(d['last']))] for d in r['unknown']])
    extra = [f"{r['blocked_attempts']} blocked attempt(s)" if r['blocked_attempts'] else '',
             f"{r['bots']} bot request(s)" if r['bots'] else '',
             f"{r['probes']} scanner probe(s)" if r['probes'] else '',
             f"{len(r['requests'])} access request(s)" if r['requests'] else '']
    if any(extra):
        print('\n' + ', '.join(x for x in extra if x).capitalize() + '.')


def main():
    ap = argparse.ArgumentParser(description='Visitors to the public landing page.')
    ap.add_argument('view', nargs='?', choices=['devices', 'requests', 'report', 'brief'])
    ap.add_argument('-n', type=int, default=30, help='how many lines (default 30)')
    ap.add_argument('--all', action='store_true', help='include bots, scanners and 404s')
    a = ap.parse_args()

    if a.view == 'brief':
        print(brief())
        return
    if a.view == 'requests':
        asks = [r for r in requests() if a.all or r['valid']][-a.n:][::-1]
        if not asks:
            print('No access requests yet.')
            return
        table(['TIME (IST)', 'EMAIL', 'LOCATION', 'DEVICE', 'IP'],
              [[stamp(r['when']), r['email'][:60] or '(empty)', r['place'], r['device'], r.get('ip', '')]
               for r in asks])
        return

    rows = load()
    if a.view == 'report':
        ensure_reports(rows)
        latest = reports()
        if latest:
            print_report(latest[0])
            mark_seen()
        else:
            print('No finished week yet; the first report comes the Monday after logging began.')
        return
    if not rows:
        print('No visits logged yet.')
        return

    res = analyse(rows=rows)
    if a.view == 'devices':
        table(['LAST SEEN', 'NAME', 'DEVICE', 'YOURS', 'VISITS', 'LOCATION', 'IP', 'FIRST SEEN'],
              [[stamp(d['active']), d['name'], d['auto'], 'yes' if d['mine'] else '', d['visits_all'],
                d['place'], d['ip'], stamp(d['first'])] for d in res['devices'][:a.n]])
    elif a.all:
        table(['TIME (IST)', 'LOCATION', 'DEVICE', 'IP', 'PAGE'],
              [[stamp(r['when']), r['place'], r['device'], r.get('ip', ''),
                ('BLOCKED ' if r['blocked'] else '') + f"{r.get('status', '')} {r.get('path', '')[:40]}"]
               for r in rows[-a.n:][::-1]])
    else:
        table(['TIME (IST)', 'LOCATION', 'DEVICE', 'FROM', 'OPEN', 'IP', ''],
              [[stamp(v['when']), v['place'], v['name'], v['source'], duration(v['open_s']), v.get('ip', ''),
                'yours' if v['mine'] else ''] for v in res['visits'][-a.n:][::-1]])

    day = datetime.now(IST) - timedelta(hours=24)
    today = [v for v in res['visits'] if v['when'] >= day]
    hidden = sum(1 for r in res['others'] if r['when'] >= day)
    print(f"\nLast 24 h: {len(today)} visit{'s' * (len(today) != 1)} from "
          f"{len({v['key'] for v in today})} device(s)"
          + (f", {hidden} bot/scanner request(s) hidden (--all)" if hidden and not a.all else '')
          + f'. Dashboard: {DASHBOARD}')
    mark_seen()


if __name__ == '__main__':
    main()
