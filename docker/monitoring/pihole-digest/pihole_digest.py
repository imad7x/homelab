#!/usr/bin/env python3
"""Weekly Pi-hole digest for the Grafana "Pi-hole Weekly" dashboard (uid pihole-weekly).

    python3 /docker/monitoring/pihole-digest/pihole_digest.py

Reads Pi-hole's long-term query database read-only through the container
(`docker exec pihole pihole-FTL sqlite3 -readonly`) and writes digest.db next to this
script: one snapshot per ISO week (Monday-Sunday, Asia/Kolkata) and one row per day.
Grafana reads digest.db read-only through the SQLite datasource (../grafana/pihole-digest.yml).

Every week still in Pi-hole's database is rebuilt on each run, so the first run backfills
and later runs refresh the current week. Pi-hole keeps 91 days; a week it has started to
prune is left as stored, so the digest keeps history Pi-hole no longer has. "New sites" (registrable
domains, reverse lookups left out) needs the four weeks before to be complete in Pi-hole, otherwise the stored rows are kept.

digest.db is built in a temporary copy and swapped in with a rename, so Grafana never
reads a half-written file. It is data, not configuration: the repo sync never copies it.
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().with_name('digest.db')
FTL = '/etc/pihole/pihole-FTL.db'
GRAVITY = '/etc/pihole/gravity.db'
TZ = 19800                       # Asia/Kolkata, UTC+05:30, no DST
DAY = 86400

# FTL query status codes (pihole-FTL enums.h)
BLOCKED = (1, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16, 18)   # lists, regex, deny, external, special domains
CACHED = (3, 17)                                       # cache, stale cache
FORWARDED = (2, 12, 13, 14)                            # forwarded, retried, already in flight
PTR = 6                                                # query type

# The Linksys router's own health checks: ~14% of all queries. Counted in the totals,
# left out of the top lists so they don't push everything else down.
NOISE = ('www.belkin.com', '156.153.64.172.in-addr.arpa', '100.34.18.104.in-addr.arpa')

# Names that mean a device is (or tries to be) resolving around Pi-hole.
BYPASS = {
    'Encrypted DNS resolver': {
        'exact': ('dns.google', 'dns.google.com', 'dns64.dns.google', 'one.one.one.one',
                  'cloudflare-dns.com', 'dns.quad9.net', 'dns9.quad9.net', 'dns10.quad9.net',
                  'dns11.quad9.net', 'dns.nextdns.io', 'doh.opendns.com', 'dns.opendns.com',
                  'doh.familyshield.opendns.com', 'dns.adguard.com', 'dns.adguard-dns.com',
                  'dns.alidns.com', 'doh.dns.sb', 'dns.mullvad.net', 'doh.mullvad.net',
                  'dns.controld.com', 'freedns.controld.com', 'doh.cleanbrowsing.org',
                  'doh.libredns.gr', 'dns.switch.ch', 'dns.digitale-gesellschaft.ch', 'doh.pub',
                  'dns.pub', 'doh.360.cn'),
        'suffix': ('.cloudflare-dns.com', '.dns.nextdns.io', '.adguard-dns.com', '.dns.mullvad.net',
                   '.cleanbrowsing.org', '.controld.com'),
    },
    'iCloud Private Relay': {'exact': ('mask.icloud.com', 'mask-h2.icloud.com', 'mask-api.icloud.com'),
                             'suffix': ()},
    'Firefox DoH check': {'exact': ('use-application-dns.net',), 'suffix': ()},
}
TWO_LEVEL = {'co', 'com', 'net', 'org', 'gov', 'ac', 'edu', 'res', 'gen', 'firm', 'ind', 'nic', 'mil'}

TOP_N = 25
NEW_N = 40
NEW_LOOKBACK = 28 * DAY


def ftl(sql, db=FTL):
    """Run read-only SQL inside the pihole container and return rows as dicts."""
    out = subprocess.run(['docker', 'exec', 'pihole', 'pihole-FTL', 'sqlite3', '-readonly', '-json', db, sql],
                         check=True, capture_output=True, text=True, timeout=600).stdout
    return json.loads(out) if out.strip() else []


def sql_list(values):
    return ','.join(map(str, values)) if isinstance(values[0], int) else \
        ','.join("'" + v.replace("'", "''") + "'" for v in values)


WK = "date(timestamp + %d, 'unixepoch', '-6 days', 'weekday 1')" % TZ
DY = "date(timestamp + %d, 'unixepoch')" % TZ
IS_BLOCKED = f"status IN ({sql_list(BLOCKED)})"
NOISE_IDS = f"(SELECT id FROM domain_by_id WHERE domain IN ({sql_list(NOISE)}))"


def site_of(domain):
    """Registrable part of a host name, close enough without the public suffix list."""
    if domain.endswith('.arpa'):
        return 'arpa'
    labels = domain.split('.')
    if len(labels) >= 3 and labels[-2] in TWO_LEVEL and len(labels[-1]) == 2:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def private_ptr(name):
    """True for reverse lookups of RFC 1918, CGNAT/Tailscale, link-local and ULA addresses."""
    if name.endswith('.in-addr.arpa'):
        octets = name[:-len('.in-addr.arpa')].split('.')[::-1]
        try:
            a = int(octets[0])
            b = int(octets[1]) if len(octets) > 1 else None
        except ValueError:
            return False
        return a == 10 or (a == 172 and b is not None and 16 <= b <= 31) or (a == 192 and b == 168) \
            or (a == 100 and b is not None and 64 <= b <= 127) or (a == 169 and b == 254)
    if name.endswith('.ip6.arpa'):
        nib = name[:-len('.ip6.arpa')].split('.')[::-1]
        return nib[:2] in (['f', 'c'], ['f', 'd']) or (nib[:2] == ['f', 'e'] and len(nib) > 2
                                                         and nib[2] in '89ab')
    return False


def client_names():
    """IP -> friendly name: Pi-hole's own names, then Docker containers on pihole_net."""
    names = {r['ip']: r['name'] for r in ftl("SELECT ip, name FROM network_addresses WHERE name <> ''")}
    names.update({'127.0.0.1': 'Pi-hole itself', '192.168.0.10': 'lab server',
                  '172.20.0.1': 'lab server (host and other containers)'})
    try:
        ids = subprocess.run(['docker', 'ps', '-q'], check=True, capture_output=True, text=True,
                             timeout=30).stdout.split()
        out = subprocess.run(['docker', 'inspect', '-f', '{{.Name}}{{range .NetworkSettings.Networks}} '
                              '{{.IPAddress}}{{end}}', *ids],
                             check=True, capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            name, *ips = line.split()
            for ip in ips:
                names.setdefault(ip, f'{name.lstrip("/")} (container)')
    except (subprocess.SubprocessError, ValueError):
        pass
    return names


SCHEMA = """
CREATE TABLE IF NOT EXISTS weeks (
    week_start TEXT PRIMARY KEY,      -- Monday, Asia/Kolkata
    week_end TEXT,                    -- Sunday
    label TEXT,                       -- '29 Sep - 5 Oct 2026'
    complete INTEGER,                 -- 1 once the Sunday is over
    data_from INTEGER, data_to INTEGER,   -- first and last query of the week, unix seconds
    queries INTEGER, blocked INTEGER, cached INTEGER, forwarded INTEGER,
    pct_blocked REAL, pct_cached REAL,
    reply_avg_ms REAL, reply_max_ms REAL, -- upstream answers only
    domains INTEGER, clients INTEGER,
    new_sites INTEGER,                -- NULL until the four weeks before are complete
    bypass_lookups INTEGER, ptr_private_upstream INTEGER,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS days (
    day TEXT PRIMARY KEY, week_start TEXT, time INTEGER,
    queries INTEGER, blocked INTEGER, cached INTEGER, forwarded INTEGER, reply_avg_ms REAL
);
CREATE TABLE IF NOT EXISTS top_domains (
    week_start TEXT, kind TEXT, rank INTEGER, domain TEXT, queries INTEGER, clients INTEGER,
    PRIMARY KEY (week_start, kind, rank)
);
CREATE TABLE IF NOT EXISTS new_sites (
    week_start TEXT, rank INTEGER, site TEXT, queries INTEGER, blocked INTEGER,
    hosts INTEGER, example TEXT, clients INTEGER,
    PRIMARY KEY (week_start, rank)
);
CREATE TABLE IF NOT EXISTS clients (
    week_start TEXT, rank INTEGER, ip TEXT, name TEXT, queries INTEGER, blocked INTEGER,
    pct_blocked REAL, PRIMARY KEY (week_start, ip)
);
CREATE TABLE IF NOT EXISTS bypass (
    week_start TEXT, ip TEXT, name TEXT, kind TEXT, domain TEXT, queries INTEGER, blocked INTEGER
);
CREATE TABLE IF NOT EXISTS ptr_upstream (
    week_start TEXT, ip TEXT, name TEXT, queries INTEGER, addresses INTEGER
);
CREATE TABLE IF NOT EXISTS lists (
    id INTEGER PRIMARY KEY, address TEXT, comment TEXT, enabled INTEGER,
    domains INTEGER, invalid INTEGER, status TEXT, updated INTEGER
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
LIST_STATUS = {0: 'unknown', 1: 'updated', 2: 'unchanged', 3: 'download failed, using cache',
               4: 'download failed, no cache'}


def label(start):
    end = start + datetime.timedelta(days=6)
    first = f'{start.day} {start:%b}' + ('' if start.year == end.year else f' {start.year}')
    return f'{first} - {end.day} {end:%b %Y}'


def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(seconds=TZ)))
    today = now.date()
    names = client_names()

    # ---------------------------------------------------------------- per week and day
    weekly = {r['wk']: r for r in ftl(f"""
        SELECT {WK} wk, count(*) queries, sum({IS_BLOCKED}) blocked,
               sum(status IN ({sql_list(CACHED)})) cached, sum(status IN ({sql_list(FORWARDED)})) forwarded,
               avg(CASE WHEN status = 2 AND reply_time > 0 THEN reply_time END) * 1000 reply_avg_ms,
               max(CASE WHEN status = 2 THEN reply_time END) * 1000 reply_max_ms,
               count(DISTINCT domain) domains, count(DISTINCT client) clients,
               min(timestamp) data_from, max(timestamp) data_to
        FROM query_storage GROUP BY wk""")}
    if not weekly:
        raise SystemExit('Pi-hole returned no queries')
    daily = ftl(f"""
        SELECT {DY} day, {WK} wk, count(*) queries, sum({IS_BLOCKED}) blocked,
               sum(status IN ({sql_list(CACHED)})) cached, sum(status IN ({sql_list(FORWARDED)})) forwarded,
               avg(CASE WHEN status = 2 AND reply_time > 0 THEN reply_time END) * 1000 reply_avg_ms
        FROM query_storage GROUP BY day""")
    ftl_from = min(r['data_from'] for r in weekly.values())

    # ------------------------------------------------- every (week, domain) with counts
    pairs = ftl(f"""
        SELECT {WK} wk, d.domain, count(*) n, sum({IS_BLOCKED}) blocked, count(DISTINCT client) clients
        FROM query_storage q JOIN domain_by_id d ON d.id = q.domain
        GROUP BY wk, q.domain""")
    by_week = defaultdict(list)
    for r in pairs:
        by_week[r['wk']].append(r)

    # ---------------------------------------------------------------------- clients
    client_rows = ftl(f"""
        SELECT {WK} wk, c.ip, count(*) n, sum({IS_BLOCKED}) blocked
        FROM query_storage q JOIN client_by_id c ON c.id = q.client
        GROUP BY wk, c.ip""")

    # ------------------------------------------------- encrypted-DNS and relay lookups
    ids = []
    for spec in BYPASS.values():
        conds = [f"domain IN ({sql_list(spec['exact'])})"] + [f"domain LIKE '%{s}'" for s in spec['suffix']]
        ids.append(' OR '.join(conds))
    bypass_rows = ftl(f"""
        SELECT {WK} wk, c.ip, d.domain, count(*) n, sum({IS_BLOCKED}) blocked
        FROM query_storage q JOIN domain_by_id d ON d.id = q.domain JOIN client_by_id c ON c.id = q.client
        WHERE q.domain IN (SELECT id FROM domain_by_id WHERE {' OR '.join('(' + i + ')' for i in ids)})
        GROUP BY wk, c.ip, d.domain""")

    # --------------------------------------- reverse lookups of private addresses upstream
    ptr_rows = ftl(f"""
        SELECT {WK} wk, c.ip, d.domain, count(*) n
        FROM query_storage q JOIN domain_by_id d ON d.id = q.domain JOIN client_by_id c ON c.id = q.client
        WHERE q.type = {PTR} AND q.status IN ({sql_list(FORWARDED)})
          AND (d.domain LIKE '%.in-addr.arpa' OR d.domain LIKE '%.ip6.arpa')
        GROUP BY wk, c.ip, d.domain""")

    # -------------------------------------------------------------------- blocklists
    lists = ftl("SELECT id, address, comment, enabled, number, invalid_domains, status, date_updated "
                "FROM adlist ORDER BY id", GRAVITY)
    gravity_size = ftl("SELECT count(DISTINCT domain) n FROM gravity", GRAVITY)[0]['n']

    # ------------------------------------------------------------------ write digest.db
    tmp = DB.with_suffix('.db.tmp')
    if DB.exists():
        shutil.copyfile(DB, tmp)
    elif tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute('PRAGMA journal_mode=DELETE')
    con.executescript(SCHEMA)
    stored = {r[0]: r[1] for r in con.execute('SELECT week_start, data_from FROM weeks')}
    stamp = now.isoformat(timespec='seconds')
    written, kept = [], []

    def kind_of(domain):
        for kind, spec in BYPASS.items():
            if domain in spec['exact'] or domain.endswith(spec['suffix']):
                return kind
        return None

    for wk in sorted(weekly):
        w = weekly[wk]
        start = datetime.date.fromisoformat(wk)
        start_ts = int(datetime.datetime(start.year, start.month, start.day,
                                         tzinfo=datetime.timezone.utc).timestamp()) - TZ
        if wk in stored and w['data_from'] > stored[wk] + 60:
            kept.append(wk)                 # Pi-hole has pruned the start of this week
            continue

        rows = by_week[wk]
        top = {'blocked': sorted((r for r in rows if r['blocked'] and r['domain'] not in NOISE),
                                 key=lambda r: -r['blocked'])[:TOP_N],
               'allowed': sorted((r for r in rows if r['n'] - r['blocked'] and r['domain'] not in NOISE),
                                 key=lambda r: -(r['n'] - r['blocked']))[:TOP_N]}

        # New sites: registrable domains seen this week and in none of the four weeks before.
        history_ok = ftl_from <= start_ts - NEW_LOOKBACK + DAY
        new_count = None
        if history_ok:
            prior = {site_of(r['domain']) for p in weekly
                     if start - datetime.timedelta(days=28) <= datetime.date.fromisoformat(p) < start
                     for r in by_week[p]}
            sites = defaultdict(lambda: {'n': 0, 'blocked': 0, 'hosts': [], 'clients': 0})
            for r in rows:
                s = site_of(r['domain'])
                if s in prior or r['domain'] in NOISE or s.endswith('.arpa'):   # reverse lookups aren't sites
                    continue
                agg = sites[s]
                agg['n'] += r['n']
                agg['blocked'] += r['blocked']
                agg['hosts'].append((r['n'], r['domain']))
                agg['clients'] = max(agg['clients'], r['clients'])
            new_count = len(sites)

        bypass = [r for r in bypass_rows if r['wk'] == wk]
        ptr = defaultdict(lambda: [0, 0])
        for r in ptr_rows:
            if r['wk'] == wk and private_ptr(r['domain']):
                ptr[r['ip']][0] += r['n']
                ptr[r['ip']][1] += 1
        clients = sorted((r for r in client_rows if r['wk'] == wk), key=lambda r: -r['n'])

        end = start + datetime.timedelta(days=6)
        if not history_ok:                  # keep what an earlier run with full history found
            prev = con.execute('SELECT new_sites FROM weeks WHERE week_start = ?', (wk,)).fetchone()
            new_count = prev[0] if prev else None
        con.execute('DELETE FROM weeks WHERE week_start = ?', (wk,))
        con.execute('INSERT INTO weeks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            wk, end.isoformat(), label(start), int(end < today), w['data_from'], w['data_to'],
            w['queries'], w['blocked'], w['cached'], w['forwarded'],
            round(100 * w['blocked'] / w['queries'], 2), round(100 * w['cached'] / w['queries'], 2),
            round(w['reply_avg_ms'] or 0, 1), round(w['reply_max_ms'] or 0, 1), w['domains'], w['clients'],
            new_count, sum(r['n'] for r in bypass if kind_of(r['domain']) == 'Encrypted DNS resolver'),
            sum(v[0] for v in ptr.values()), stamp))

        con.execute('DELETE FROM top_domains WHERE week_start = ?', (wk,))
        for kind, lst in top.items():
            con.executemany('INSERT INTO top_domains VALUES (?,?,?,?,?,?)', [
                (wk, kind, i + 1, r['domain'], r['blocked'] if kind == 'blocked' else r['n'] - r['blocked'],
                 r['clients']) for i, r in enumerate(lst)])

        if history_ok:
            con.execute('DELETE FROM new_sites WHERE week_start = ?', (wk,))
            ranked = sorted(sites.items(), key=lambda kv: -kv[1]['n'])[:NEW_N]
            con.executemany('INSERT INTO new_sites VALUES (?,?,?,?,?,?,?,?)', [
                (wk, i + 1, s, a['n'], a['blocked'], len(a['hosts']), max(a['hosts'])[1], a['clients'])
                for i, (s, a) in enumerate(ranked)])

        con.execute('DELETE FROM clients WHERE week_start = ?', (wk,))
        con.executemany('INSERT INTO clients VALUES (?,?,?,?,?,?,?)', [
            (wk, i + 1, r['ip'], names.get(r['ip'], ''), r['n'], r['blocked'],
             round(100 * r['blocked'] / r['n'], 1)) for i, r in enumerate(clients)])

        con.execute('DELETE FROM bypass WHERE week_start = ?', (wk,))
        con.executemany('INSERT INTO bypass VALUES (?,?,?,?,?,?,?)', [
            (wk, r['ip'], names.get(r['ip'], ''), kind_of(r['domain']), r['domain'], r['n'], r['blocked'])
            for r in bypass])

        con.execute('DELETE FROM ptr_upstream WHERE week_start = ?', (wk,))
        con.executemany('INSERT INTO ptr_upstream VALUES (?,?,?,?,?)', [
            (wk, ip, names.get(ip, ''), v[0], v[1]) for ip, v in ptr.items()])
        written.append(wk)

    for r in daily:
        if r['wk'] in written:
            d = datetime.date.fromisoformat(r['day'])
            ts = int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp()) - TZ
            con.execute('INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?)', (
                r['day'], r['wk'], ts, r['queries'], r['blocked'], r['cached'], r['forwarded'],
                round(r['reply_avg_ms'] or 0, 1)))

    con.execute('DELETE FROM lists')
    con.executemany('INSERT INTO lists VALUES (?,?,?,?,?,?,?,?)', [
        (r['id'], r['address'], r['comment'] or '', r['enabled'], r['number'], r['invalid_domains'],
         LIST_STATUS.get(r['status'], str(r['status'])), r['date_updated']) for r in lists])
    con.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [
        ('updated_at', stamp), ('gravity_domains', str(gravity_size)),
        ('pihole_data_from', str(ftl_from))])
    con.commit()
    con.close()
    os.chmod(tmp, 0o644)
    os.replace(tmp, DB)
    print(f'{stamp} digest: rebuilt {len(written)} week(s) {written[0] if written else ""}..'
          f'{written[-1] if written else ""}, kept {len(kept)} pruned week(s)')


if __name__ == '__main__':
    main()
