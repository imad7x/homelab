#!/usr/bin/env python3
"""Cloudflare's side of the visitor log: every request to the domain and its subdomains,
including the ones Cloudflare answered itself (its login page, its block rules), which
never reach this server.

Cloudflare keeps per-request logs for 31 days on every plan. Once an hour this pulls the
new ones through the GraphQL analytics API and keeps, for good, in ../visitors-state/cloudflare/:

  networks.json        IP -> [network number, network name, country], for every IP seen
  apps-YYYY-MM.jsonl   requests to the subdomains, grouped into bursts per IP + browser
                       + subdomain (30 min gap): counts, what Cloudflare did with them,
                       sign-in attempts, a few paths. Query strings are never kept (they
                       carry app tokens); only a music app's username (Subsonic "u=").
  access-YYYY-MM.jsonl sign-ins at Cloudflare's login page (email, IP, app, allowed),
                       which Cloudflare itself keeps only for a short while.
  state.json           how far it got, the zone/account ids, the last error, and who
                       Cloudflare's login lets in where (to tell your own sign-ins apart).

Needs CF_API_TOKEN (read-only: Analytics + Zone; Access audit logs for the sign-ins), from
the environment or ../cloudflare.env. `visitors pull` runs it by hand.
"""
import json, os, re, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import visitors as V

API = 'https://api.cloudflare.com/client/v4'
DIR = os.path.join(V.STATE, 'cloudflare')
LAG = timedelta(minutes=15)       # Cloudflare's analytics can trail by a few minutes
KEEP = timedelta(days=30)         # how far back the first pull reaches (Cloudflare keeps 31)
WINDOW = timedelta(hours=6)       # one query; halved while a window hits the row limit
LIMIT = 10000
GAP = 1800                        # seconds between requests that still count as one burst
FIELDS = ('datetime clientIP clientRequestHTTPHost clientRequestPath clientRequestQuery '
          'clientRequestHTTPMethodName edgeResponseStatus originResponseStatus clientAsn '
          'clientASNDescription clientCountryName userAgent securityAction')
SIGN_IN = re.compile(r'auth|login|signin|sign-in|quickconnect', re.I)
# Requests that only answer for a signed-in user of the app itself (Jellyfin, Navidrome).
APP_USE = re.compile(r'^/(users/[0-9a-f]{32}|items|shows|sessions/playing|videos|audio|rest/(?!ping))', re.I)


def token():
    tok = os.environ.get('CF_API_TOKEN', '').strip()
    if not tok:
        try:
            with open(os.path.join(V.BASE, 'cloudflare.env'), encoding='utf-8') as fh:
                for line in fh:
                    if line.startswith('CF_API_TOKEN='):
                        tok = line.split('=', 1)[1].strip().strip('"\'')
        except OSError:
            pass
    return tok


def call(path, tok, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read() or b'{}')
        except ValueError:
            out = {}
        out.setdefault('errors', [{'message': f'HTTP {e.code}'}])
        return out


def iso(t):
    return t.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def ids(tok, st):
    """Zone and account ids, looked up once from the domain name."""
    if st.get('zone') and st.get('account'):
        return st['zone'], st['account']
    r = call(f'/zones?name={V.SITE}', tok)
    zones = r.get('result') or []
    if not zones:
        raise RuntimeError(f"zone lookup failed: {r.get('errors')}")
    st['zone'], st['account'] = zones[0]['id'], zones[0]['account']['id']
    return st['zone'], st['account']


def requests_between(tok, zone, start, end):
    """Every request in [start, end), oldest first; splits windows that hit the row limit."""
    q = ('query($f:ZoneHttpRequestsAdaptiveFilter_InputObject){viewer{zones(filter:{zoneTag:"%s"}){'
         'httpRequestsAdaptive(filter:$f,limit:%d,orderBy:[datetime_ASC]){%s}}}}' % (zone, LIMIT, FIELDS))
    out, t = [], start
    while t < end:
        u = min(t + WINDOW, end)
        stack = [(t, u)]
        while stack:
            a, b = stack.pop()
            r = call('/graphql', tok, {'query': q, 'variables': {'f': {'datetime_geq': iso(a), 'datetime_lt': iso(b)}}})
            if r.get('errors'):
                raise RuntimeError(f"analytics query failed: {r['errors']}")
            rows = r['data']['viewer']['zones'][0]['httpRequestsAdaptive']
            if len(rows) >= LIMIT and b - a > timedelta(minutes=10):
                mid = a + (b - a) / 2
                stack += [(mid, b), (a, mid)]   # popped oldest half first
                continue
            out += rows
        t = u
    return out


def bursts(rows):
    """Requests to the subdomains as bursts: one per IP + browser + subdomain, split at
    30 minutes of nothing."""
    open_, done = {}, []
    for r in rows:
        host = (r.get('clientRequestHTTPHost') or '').split(':')[0].lower()
        path = r.get('clientRequestPath') or '/'
        if host == V.SITE or not host.endswith('.' + V.SITE) or path.startswith('/cdn-cgi/'):
            continue
        ts = parse(r['datetime'])
        key = (r.get('clientIP') or '', r.get('userAgent') or '', host)
        b = open_.get(key)
        if b is None or (ts - b['_e']).total_seconds() > GAP:
            if b is not None:
                done.append(b)
            b = open_[key] = {'s': r['datetime'], 'e': r['datetime'], 'host': host, 'ip': key[0], 'ua': key[1],
                              'asn': int(r.get('clientAsn') or 0), 'net': r.get('clientASNDescription') or '',
                              'cc': r.get('clientCountryName') or '', 'n': 0, 'server': 0, 'gate': 0, 'blocked': 0,
                              'used': 0, 'paths': [], 'auth': [], 'users': [], '_e': ts}
        b['e'], b['_e'] = r['datetime'], ts
        b['n'] += 1
        status, origin = int(r.get('edgeResponseStatus') or 0), int(r.get('originResponseStatus') or 0)
        method = r.get('clientRequestHTTPMethodName') or ''
        if origin:
            b['server'] += 1
            if 200 <= status < 300 and APP_USE.match(path):
                b['used'] += 1
        elif r.get('securityAction') == 'block' or status == 403:
            b['blocked'] += 1
        elif status in (302, 401):   # Cloudflare's login page (Access)
            b['gate'] += 1
        if len(b['paths']) < 8 and path not in b['paths']:
            b['paths'].append(path[:120])
        if method == 'POST' and SIGN_IN.search(path) and len(b['auth']) < 20:
            b['auth'].append([r['datetime'], path[:80], status])
        if path.startswith('/rest/'):
            user = urllib.parse.parse_qs(r.get('clientRequestQuery') or '').get('u', [''])[0][:40]
            if user and user not in b['users']:
                b['users'].append(user)
    done += open_.values()
    for b in done:
        del b['_e']
    return sorted(done, key=lambda b: b['s'])


def append(kind, items, when):
    by_month = {}
    for it in items:
        by_month.setdefault(parse(when(it)).strftime('%Y-%m'), []).append(it)
    os.makedirs(DIR, exist_ok=True)
    for month, rows in by_month.items():
        path = os.path.join(DIR, f'{kind}-{month}.jsonl')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))
        os.chmod(path, 0o600)


def access_logins(tok, account, start, end):
    """Sign-ins at Cloudflare's login page. A failure here is not fatal: the token may
    not have the Access audit-log permission."""
    out, since = [], start
    for _ in range(20):
        q = urllib.parse.urlencode({'since': iso(since), 'until': iso(end), 'limit': 1000, 'direction': 'asc'})
        r = call(f'/accounts/{account}/access/logs/access_requests?{q}', tok)
        if r.get('errors') and not r.get('success'):
            return out, f"sign-in log: {r['errors'][0].get('message', r['errors'])}"
        rows = r.get('result') or []
        out += [{'t': x.get('created_at', ''), 'email': x.get('user_email', ''), 'ip': x.get('ip_address', ''),
                 'app': x.get('app_domain', ''), 'allowed': bool(x.get('allowed')), 'action': x.get('action', ''),
                 'country': x.get('country', ''), 'via': x.get('connection', '')} for x in rows]
        if len(rows) < 1000:
            break
        since = parse(rows[-1]['created_at']) + timedelta(seconds=1)
    return out, None


def access_rules(tok, account):
    """Who Cloudflare's login lets in where. 'public': {host: [path, ...]} that everyone
    gets through (bypass policies); 'allowed': {host or *.domain: [emails]} for apps whose
    policies name single emails only (None there = groups, whole email domains or
    everyone, so nothing to conclude). None if the token cannot read them."""
    r = call(f'/accounts/{account}/access/apps', tok)
    if not r.get('success'):
        return None
    public, allowed = {}, {}
    for app in r.get('result') or []:
        policies = app.get('policies') or []
        for d in app.get('self_hosted_domains') or [app.get('domain')]:
            host, _, path = (d or '').lower().partition('/')
            if any(p.get('decision') == 'bypass' for p in policies):
                public.setdefault(host, []).append('/' + path)
            elif not path and host:
                emails, other = [], False
                for p in policies:
                    if p.get('decision') != 'allow':
                        continue
                    for rule in p.get('include') or []:
                        if 'email' in rule:
                            emails.append(rule['email'].get('email', ''))
                        else:
                            other = True
                allowed[host] = None if other or not emails else sorted(set(emails))
    return {'public': public, 'allowed': allowed}


def pull(now=None):
    """Fetch everything new since the last run. Returns a one-line summary."""
    tok = token()
    if not tok:
        return 'no CF_API_TOKEN: Cloudflare data not pulled'
    path = os.path.join(DIR, 'state.json')
    st = V.read_json(path, {})
    now = now or datetime.now(timezone.utc)
    end = now - LAG
    start = parse(st['until']) if st.get('until') else now - KEEP
    start = max(start, now - KEEP)
    if end - start < timedelta(minutes=5):
        end = start   # too soon for new requests; still refresh the login rules
    try:
        zone, account = ids(tok, st)
        rows = requests_between(tok, zone, start, end)
        nets = V.read_json(os.path.join(DIR, 'networks.json'), {})
        for r in rows:
            if r.get('clientIP'):
                nets[r['clientIP']] = [int(r.get('clientAsn') or 0), r.get('clientASNDescription') or '',
                                       r.get('clientCountryName') or '']
        found = bursts(rows)
        append('apps', found, lambda b: b['s'])
        V.write_json(os.path.join(DIR, 'networks.json'), nets)
        logins, warn = access_logins(tok, account, start, end) if end > start else ([], None)
        append('access', logins, lambda x: x['t'])
        rules = access_rules(tok, account)
        if rules is not None:
            st.update(rules)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, IndexError) as e:
        st.update(error=str(e)[:300], tried=iso(now))
        V.write_json(path, st)
        return f'pull failed: {e}'
    st.update(until=iso(end), ok=iso(now), error=warn, tried=iso(now))
    V.write_json(path, st)
    return (f'{len(rows)} requests {start:%d %b %H:%M}–{end:%d %b %H:%M} UTC, {len(found)} subdomain bursts, '
            f'{len(logins)} sign-ins' + (f' ({warn})' if warn else ''))


if __name__ == '__main__':
    print(pull())
