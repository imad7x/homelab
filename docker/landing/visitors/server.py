#!/usr/bin/env python3
"""LAN dashboard for the landing page's visitors (port 3011; never give it a tunnel route).

Serves web/ and a small JSON API over visitors.py: who visited, visits per device,
marking devices as yours, the blocklist nginx enforces, and the weekly reports, which
it writes every Monday for the week before.

Only the Host names in ALLOWED_HOSTS are answered (stops DNS-rebinding pages reading it
through a browser on the LAN), and changes need the page's own header and origin.
"""
import json, os, threading, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import visitors as V

WEB = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'web')
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
ALLOWED = {h.strip() for h in os.environ.get('ALLOWED_HOSTS', 'localhost:3011').split(',') if h.strip()}
RANGES = {'7': 7, '30': 30, '90': 90, 'all': None}
LOCK = threading.Lock()
HEADERS = {
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
    'Cache-Control': 'no-store',
}


def iso(t):
    return t.isoformat(timespec='seconds') if t else None


def device_json(x):
    return {
        'key': x['key'], 'name': x['name'], 'auto': x['auto'], 'mine': x['mine'], 'cookie': x['cookie'],
        'visits': x['visits'], 'visits_all': x['visits_all'], 'attempts': x['attempts'],
        'first': iso(x['first']), 'last': iso(x['last']), 'active': iso(x['active']), 'ip': x['ip'],
        'place': x['place'], 'area': x['area'], 'source': x['source'], 'language': x['language'], 'colo': x['colo'],
        'ips': sorted(([ip, iso(t)] for ip, t in x['ips'].items()), key=lambda p: p[1], reverse=True),
        'places': list(x['places']), 'blocked': x['blocked'], 'your_network': x['your_network'],
    }


def data(rng):
    days = RANGES.get(rng, 7)
    now = datetime.now(V.IST)
    rows = V.load()
    # "7 days" = today and the six days before it, from midnight IST.
    start = datetime(now.year, now.month, now.day, tzinfo=V.IST) - timedelta(days=days - 1) if days else None
    a = V.analyse(start, None, rows)
    devices = a['devices']

    first_day = start or (a['visits'][0]['when'] if a['visits'] else now)
    day0 = datetime(first_day.year, first_day.month, first_day.day, tzinfo=V.IST)
    series, d = {}, day0
    while d <= now:
        series[d.date().isoformat()] = [0, 0]
        d += timedelta(days=1)
    for v in a['visits']:
        k = v['when'].date().isoformat()
        if k in series:
            series[k][0 if v['mine'] else 1] += 1

    asks = V.requests(start)
    return {
        'generated': iso(now), 'range': rng, 'tz': 'Asia/Kolkata',
        'totals': {
            'visits': len(a['visits']), 'devices': sum(1 for x in devices if x['visits']),
            'unknown_devices': sum(1 for x in devices if x['visits'] and not x['mine']),
            'unknown_visits': sum(1 for v in a['visits'] if not v['mine']),
            'mine_visits': sum(1 for v in a['visits'] if v['mine']),
            'attempts': len(a['attempts']),
            'bots': sum(1 for r in a['others'] if r['bot']),
            'probes': sum(1 for r in a['others'] if not r['bot']),
            'requests': sum(1 for r in asks if r['valid']),
        },
        'devices': [device_json(x) for x in devices],
        'days': [[k, m, u] for k, (m, u) in series.items()],
        'visits': [{'when': iso(v['when']), 'key': v['key'], 'name': v['name'], 'mine': v['mine'],
                    'device': v['device'], 'place': v['place'], 'ip': v.get('ip', ''), 'source': v['source'],
                    'open_s': v['open_s'], 'open_partial': v.get('open_partial', False), 'language': v['language'], 'colo': v['colo']}
                   for v in a['visits'][-300:][::-1]],
        'others': [{'when': iso(r['when']), 'label': r['device'], 'bot': r['bot'], 'place': r['place'],
                    'ip': r.get('ip', ''), 'path': (r.get('path') or '')[:80], 'status': str(r.get('status', ''))}
                   for r in a['others'][-300:][::-1]],
        'attempts': [{'when': iso(r['when']), 'key': r['key'], 'name': a['all_devices'].get(r['key'], {}).get('name', r['device']),
                      'place': r['place'], 'ip': r.get('ip', ''), 'path': (r.get('path') or '')[:80]}
                     for r in a['attempts'][-200:][::-1]],
        'requests': [{'when': iso(r['when']), 'email': r['email'][:120], 'valid': r['valid'], 'place': r['place'],
                      'device': r['device'], 'ip': r.get('ip', '')} for r in asks[::-1]],
        'blocklist': a['blocklist'],
        'reports': V.reports()[:12],
    }


def block(body):
    """Device: its id cookie only (an IP is often shared: home Wi-Fi, a mobile network).
    A one-off visitor without an id, or an IP typed into the form, blocks the IP; never
    one that your marked devices have used."""
    kind, value = body.get('kind'), str(body.get('value') or '').strip()
    a = V.analyse()
    mine_ips = {ip: d['name'] for d in a['all_devices'].values() if d['mine'] for ip in d['ips']}
    entries = V.blocklist()
    now = datetime.now(V.IST).isoformat(timespec='seconds')
    added = []

    def add(k, v, note):
        if not any(e['kind'] == k and e['value'] == v for e in entries):
            entries.append({'kind': k, 'value': v, 'note': note[:80], 'at': now})
            added.append(v)

    def add_ip(ip, note):
        net = V.parse_net(ip)
        hit = next((name for mip, name in mine_ips.items() if V.is_blocked([{'kind': 'ip', 'value': net}], ip=mip)), None)
        if hit:
            raise ValueError(f'{net} is also used by your device "{hit}", so it was not blocked')
        add('ip', net, note)

    if kind == 'device':
        d = a['all_devices'].get(value)
        if not d:
            raise ValueError('unknown device')
        if d['mine']:
            raise ValueError('that device is marked as yours')
        if d['cookie']:
            add('device', d['key'][2:], d['name'])
        elif d['ip']:
            add_ip(d['ip'], f"{d['name']} (one-off visit)")
        else:
            raise ValueError('nothing to block for that device')
    elif kind == 'ip':
        add_ip(value, str(body.get('note') or 'added by hand'))
    else:
        raise ValueError('nothing to block')
    V.write_blocklist(entries)
    return {'added': added}


def unblock(body):
    kind, value = body.get('kind'), str(body.get('value') or '').strip()
    if kind == 'ip':
        value = V.parse_net(value)   # "1.2.3.4" is stored as "1.2.3.4/32"
    entries = [e for e in V.blocklist() if not (e['kind'] == kind and e['value'] == value)]
    V.write_blocklist(entries)
    return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    server_version = 'visitors'
    sys_version = ''

    def log_message(self, fmt, *args):  # quiet: one line per change only
        pass

    def send(self, code, body, ctype='application/json'):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        if self.headers.get('Host', '') not in ALLOWED:
            self.send(421, {'error': 'unknown host'})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            with open(os.path.join(WEB, name), 'rb') as fh:
                return self.send(200, fh.read(), ctype)
        if url.path == '/api/device':
            try:
                with LOCK:
                    d, sessions = V.activity(parse_qs(url.query).get('key', [''])[0])
            except ValueError as e:
                return self.send(404, {'error': str(e)})
            return self.send(200, {'device': device_json(d), 'sessions': sessions})
        if url.path == '/api/data':
            rng = parse_qs(url.query).get('range', ['7'])[0]
            with LOCK:
                out = data(rng if rng in RANGES else '7')
                V.mark_seen()
            return self.send(200, out)
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        if not self.allowed():
            return
        origin = self.headers.get('Origin')
        if self.headers.get('X-Visitors') != '1' or (origin and origin != f"http://{self.headers['Host']}"):
            return self.send(403, {'error': 'forbidden'})
        try:
            size = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(size, 4096)) or b'{}') if size <= 4096 else None
            if not isinstance(body, dict):
                raise ValueError('bad request')
            path = urlsplit(self.path).path
            with LOCK:
                if path == '/api/device':
                    V.set_label(body.get('key'), body.get('name'), body.get('mine'))
                    out = {'ok': True}
                elif path == '/api/block':
                    out = block(body)
                elif path == '/api/unblock':
                    out = unblock(body)
                else:
                    return self.send(404, {'error': 'not found'})
            print(f"{datetime.now(V.IST):%Y-%m-%d %H:%M} {path} {json.dumps(body)[:200]}", flush=True)
            self.send(200, out)
        except ValueError as e:
            self.send(400, {'error': str(e)})


def reporter():
    """Write last week's report soon after Monday 00:00 IST (checks every 10 minutes)."""
    while True:
        try:
            with LOCK:
                for path in V.ensure_reports():
                    print(f'wrote {path}', flush=True)
        except Exception as e:  # keep the dashboard up whatever a report trips on
            print(f'report failed: {e!r}', flush=True)
        time.sleep(600)


if __name__ == '__main__':
    threading.Thread(target=reporter, daemon=True).start()
    print(f'visitors dashboard on :8080 for {sorted(ALLOWED)}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
