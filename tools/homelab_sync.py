#!/usr/bin/env python3
"""Keep a public, secret-free copy of this homelab's configuration in git.

Live files are copied into the repo with every password, API key, token and personal
detail replaced by a placeholder: [[secret:NAME]] or [[private:NAME]]. The real values,
plus the files that are private as a whole (.env files, the finance ingest code, Home
Assistant's .storage), are packed per stack into tar.gz bundles, encrypted with age and
stored as GitHub Actions secrets named BUNDLE_<STACK>_<NN>.

  homelab_sync.py sync [--push]   refresh the repo from the live box and run the leak checks;
                                  --push also commits, pushes and uploads changed bundles
  homelab_sync.py check           run the leak checks against the repo as it is now
  homelab_sync.py restore DIR --identity KEY [--target T]
                                  rebuild the /docker tree into T from this repo plus the
                                  encrypted bundles in DIR (host/ files go to T/_host)

Private settings live outside the repo, in ~/.config/homelab-backup/:
  age.key, recipients.txt   the age identity, and the public keys bundles are encrypted to
  private-terms.env         NAME=value personal details that must never be published
  host-private.txt          extra files outside /docker to back up in the HOST bundle only
  blocked.txt               regexes that must never appear in the repo (the check fails on them)
  state.json                hash of each bundle as last uploaded
"""
import argparse
import base64
import collections
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path('/docker')
HOME = Path.home()
CONF = HOME / '.config/homelab-backup'
GH_REPO = 'imad7x/homelab'
CHUNK = 45_000          # GitHub secrets hold at most 48 KB
MANAGED = ('docker', 'host', 'apps')   # repo folders this tool owns (exports/ is handled per app)

# ---------------------------------------------------------------- what gets copied
# Globs relative to /docker. PRIVATE wins over PUBLIC; a file matching neither is not
# copied at all (databases, media, caches, logs, runtime state, leftovers of removed apps).
PRIVATE = [
    '**/*.env', '**/.env', 'finance/secrets/*',
    'finance/ingest/*.py', 'finance/ingest/*.yaml', 'finance/ingest/*.sql',
    'finance/grafana_dashboard.py', 'finance/FORMATS.md',
    'homeassistant/config/secrets.yaml', 'homeassistant/config/.storage/*',
    'monitoring/grafana/provisioning/dashboards/finance.json',   # names banks, cards and loans
]
PUBLIC = [
    '*/compose.y*ml', '*/*/compose.y*ml',
    '*/Dockerfile', '*/*/Dockerfile', '*/.dockerignore', '*/*/requirements.txt',
    'updater/app/*',
    'monitoring/prometheus/prometheus.yml', 'monitoring/prometheus/rules/*.yml',
    'monitoring/grafana/*.yml', 'monitoring/grafana/provisioning/**/*.yml',
    'monitoring/grafana/provisioning/**/*.json', 'monitoring/dashboards-src/*.py',
    'monitoring/homepage/config/*.yaml', 'monitoring/homepage/config/*.css',
    'monitoring/homepage/config/*.js', 'monitoring/scrutiny/config/*.yaml',
    'pihole/config.yml', 'pihole/etc-pihole/pihole.toml',
    'paperless/*.smb.conf',
    'jellyfin/config/*.xml', 'jellyfin/config/logging.default.json',
    'jellyfin/config/data/plugins/configurations/*.xml',
    'jellyfin/config/data/plugins/configurations/*/*.json',
    'jellyfin/config/data/root/default/*/options.xml',
    'jellyfin/jellystat/*.sql',
    'jellyfin/servarr/*/config.xml',
    'jellyfin/servarr/qbittorrent/qBittorrent/qBittorrent.conf',
    'jellyfin/servarr/qbittorrent/qBittorrent/categories.json',
    'jellyfin/servarr/qbittorrent/qBittorrent/watched_folders.json',
    'jellyfin/servarr/bazarr/config/config.yaml',
    'jellyfin/servarr/jellyseerr/settings.json',
    'jellyfin/servarr/scripts/*.sh', 'jellyfin/servarr/scripts/*.snippet',
    'jellyfin/music/slskd-data/slskd.yml',
    'homeassistant/config/*.yaml', 'homeassistant/config/blueprints/**/*.yaml',
]
SKIP = [
    '**/__pycache__/*', '**/*.bak*', '**/*cache*.json',
    'homeassistant/config/.storage/core.restore_state', 'homeassistant/config/.storage/trace.*',
    'homeassistant/config/.storage/repairs.*', 'homeassistant/config/.storage/bluetooth.*',
    'homeassistant/config/.storage/frontend.*',
]
PRUNE = {   # big data folders never worth walking
    'jellyfin/config/metadata', 'jellyfin/config/cache', 'jellyfin/config/.cache', 'jellyfin/config/log',
    'jellyfin/config/data/data', 'jellyfin/config/data/metadata', 'jellyfin/config/data/transcodes',
    'jellyfin/config/data/temp', 'jellyfin/music/navidrome-data', 'jellyfin/servarr/prowlarr/Definitions',
    'jellyfin/jellystat/db', 'jellyfin/jellystat/backup-data', 'monitoring/scrutiny/influxdb',
    'paperless/data', 'finance/mail', 'finance/samples', 'finance/backups', 'finance/state',
    'homeassistant/config/deps', 'homeassistant/config/.cache', 'pihole/etc-pihole/listsCache',
    'updater/state', 'jellyfin/logs',
}
PRUNE_NAMES = {'__pycache__', 'MediaCover', 'Backups', 'logs', 'log', 'node_modules', '.git'}
HOST = [    # (source, repo path)
    ('/etc/fstab', 'host/etc/fstab'),
    ('/etc/samba/smb.conf', 'host/etc/samba/smb.conf'),
    ('/etc/docker/daemon.json', 'host/etc/docker/daemon.json'),
    ('/etc/netplan/50-cloud-init.yaml', 'host/etc/netplan/50-cloud-init.yaml'),
    ('/etc/systemd/system/pihole-pw.service', 'host/etc/systemd/system/pihole-pw.service'),
    ('~/pihole-pw.sh', 'host/home/pihole-pw.sh'),
]
DOCS = 'README.md'  # hand-written docs inside docker/, host/, apps/: kept by sync, skipped by restore

# ---------------------------------------------------------------- what counts as secret
SECRET_KEY = re.compile(
    r'(?i)(api_?key|passw(or)?d|pass$|_pass_|pwhash|pass_?hash|secret|token|private_?key|'
    r'cookie|passkey|webhook_?url|vapid_?private|encryption_?key|signing_?key|access_?key|'
    r'auth_?key|salt$|(^|_)key$|^pin$|_pin$|captcha|'
    r'^(openweathermap|weatherapi|finnhub)$)')     # Homepage `providers:` hold bare API keys
NOT_SECRET_KEY = re.compile(
    r'(?i)(enabled?$|required$|method$|type$|expir|whitelist|header|apikeykey|keytype|'
    r'length$|file$|path$|maxage|timeout|interval|name$|_?url_?base$|uri$|^use_|provider$)')
IDENTITY_KEY = re.compile(r'(?i)^(user(_?name)?|login|e_?mail|f_username|auth_?user|smtp_?user)$')
PUBLIC_VALUES = {'imad7x', 'admin', 'root', 'true', 'false', 'null', 'none', 'yes', 'no',
                 'welcome', 'changeme', 'basic', 'forms', 'external', 'disabledforlocaladdresses',
                 'grafana_ro', 'firefly', 'jellystat', 'paperless', 'postgres'}
CODE_SUFFIXES = {'.py', '.sh', '.js', '.html', '.css', '.sql'}

KV_LINE = re.compile(r'''^(?P<pre>[ \t]*(?:-[ \t]+)?)(?P<q1>["']?)(?P<k>[A-Za-z_][\w.\\/-]*)(?P=q1)'''
                     r'''(?P<sep>[ \t]*[:=][ \t]*)(?P<q>["']?)(?P<v>[^\n]*?)(?P=q)(?P<post>[ \t]*(?:[ \t]#[^\n]*)?)$''', re.M)
XML_EL = re.compile(r'<(?P<k>[A-Za-z_][\w.-]*)>(?P<v>[^<\n]+)</(?P=k)>')
JSON_KV = re.compile(r'"(?P<k>[^"\\\n]+)"\s*:\s*"(?P<v>(?:[^"\\\n]|\\.)*)"')
URL_CRED = re.compile(r'(?P<s>\b[a-z][a-z0-9+.-]*://)(?P<u>[^/\s:@"\'<>\[\]]*):(?P<v>[^/\s@"\'<>\[\]]+)@')
QUERY_TOK = re.compile(r'(?i)(?P<pre>[?&](?:api_?key|token|access_token|password)=)(?P<v>[^&\s"\'<>#\[\]]+)')
CUSTOM = [re.compile(r'--token\s+(?P<v>[A-Za-z0-9_=+/-]{20,})'), re.compile(r'setpassword\s+(?P<v>[^\s$]\S*)')]
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
EMAIL_OK = re.compile(r'(?i)(@(example\.(com|org|net)|users\.noreply\.github\.com|localhost)$|^(noreply|example)@|^git@github\.com$)')
VPA = re.compile(r'(?i)(?<![\w.-])[\w.-]+@(ok(axis|hdfcbank|icici|sbi)|ybl|paytm|upi|ibl|axl|apl|ptyes|ptsbi|ptaxis|pthdfc)\b')
PH = re.compile(r'\[\[(secret|private):([A-Z0-9_]+)\]\]')
PHONE = re.compile(r'(?<![\d.:/#-])(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d.:/-])')
CARD = re.compile(r'(?<![\d.])(?:[3-6]\d{14,15}|\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{3,4})(?![\d.])')
AADHAAR = re.compile(r'(?<!\d)[2-9]\d{3} \d{4} \d{4}(?!\d)')
PAN = re.compile(r'\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b')
IFSC = re.compile(r'\b[A-Z]{4}0[A-Z0-9]{6}\b')
GEO = re.compile(r'(?i)["\']?\b(lat(itude)?|lon(gitude)?|lng)\b["\']?\s*[:=]\s*["\']?-?\d{1,3}\.\d{3,}')
SAFE_ENV_VALUE = re.compile(r'(true|false|yes|no|auto|[-+]?\d+(\.\d+)?|[a-z][a-z0-9_.,:/*-]*|[A-Z][a-z]+/[A-Za-z_]+|'
                            r'[a-z]{2}_[A-Z]{2}|https?://[a-z0-9_.-]+(:\d+)?(/[\w./-]*)?|/[\w./-]+|\*)?')
STOP_TERMS = {'admin', 'guest', 'user', 'users', 'family', 'kids', 'test', 'demo', 'media', 'home', 'default',
              'owner', 'living room', 'bedroom', 'server', 'cash', 'imad7x', 'lab7x'}
# Long random-looking values: anything like this must sit under a known-safe key, or the check
# fails - this is what catches an API key under a key name nobody thought of.
RANDOM_VALUE = re.compile(r'(?<![A-Za-z0-9+/_.-])(?:[0-9a-fA-F]{32,}|(?=[A-Za-z0-9+/_-]*\d[A-Za-z0-9+/_-]*\d)'
                          r'(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*[A-Z])[A-Za-z0-9+_-][A-Za-z0-9+/_-]{23,}={0,2})'
                          r'(?![A-Za-z0-9+/_.-])')
RANDOM_OK = [   # (path regex, key regex) reviewed: ids, public keys and templates, not secrets
    (r'jellyseerr/settings\.json$', r'^(id|serverId|vapidPublic|jsonPayload)$'),
    (r'^exports/jellyfin/plugins\.json$', r'^Id$'),
]
CAMEL = r'(?:[A-Z][a-z]+[0-9]*)+|[a-z]+(?:[A-Z][a-z]+[0-9]*)+'   # setting/tag names like EnableDepth10Hevc
KEY_BEFORE = re.compile(r'["\'<]?([A-Za-z_][\w.\\-]*)["\'>]?\s*[:=>]\s*["\']?$')
PRIVATE_BLOCK = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|AGE-SECRET-KEY-1[0-9A-Z]{20,}')
APPS = [('jellyfin/servarr/radarr', 'RADARR'), ('jellyfin/servarr/sonarr', 'SONARR'),
        ('jellyfin/servarr/prowlarr', 'PROWLARR'), ('jellyfin/servarr/bazarr', 'BAZARR'),
        ('jellyfin/servarr/jellyseerr', 'SEERR'), ('jellyfin/servarr/qbittorrent', 'QBITTORRENT'),
        ('jellyfin/music', 'SLSKD'), ('jellyfin/config', 'JELLYFIN'), ('monitoring/homepage', 'HOMEPAGE'),
        ('monitoring/grafana', 'GRAFANA'), ('monitoring/scrutiny', 'SCRUTINY')]


def glob_re(pattern):
    out, i = '', 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r'\Z')


def matcher(patterns):
    rxs = [glob_re(p) for p in patterns]
    return lambda rel: any(rx.match(rel) for rx in rxs)


is_private, is_public, is_skipped = matcher(PRIVATE), matcher(PUBLIC), matcher(SKIP)


def usable(v):
    return (len(v) >= 4 and v.lower() not in PUBLIC_VALUES and not v.startswith(('${', '{', '[[', '%(', '<'))
            and not re.fullmatch(r'\$[A-Za-z_][A-Za-z0-9_]*', v)       # $VAR reference, not a $hash$
            and not re.fullmatch(r'\d{1,5}|[\d.]+|[*xX\u2022]+|\d+:\d+', v))


def secret_key(k):
    k = re.split(r'[\\/.]', k)[-1]
    return bool(SECRET_KEY.search(k)) and not NOT_SECRET_KEY.search(k)


def looks_random(v):
    return (len(v) >= 20 and ' ' not in v and not v.startswith('/') and re.search(r'[a-z]', v)
            and re.search(r'[A-Z]', v) and re.search(r'\d', v))


def app_of(rel):
    for prefix, app in APPS:
        if rel.startswith(prefix):
            return app
    return rel.split('/')[0].upper() if '/' in rel else 'ROOT'


def read_text(path):
    with open(path, encoding='utf-8', errors='surrogateescape', newline='') as f:
        return f.read()


def read_bytes_any(path):
    """Read a file, falling back to a throwaway container for root-only files."""
    p = Path(path)
    try:
        return p.read_bytes()
    except PermissionError:
        return subprocess.run(['docker', 'run', '--rm', '-v', f'{p.parent}:/x:ro', 'alpine:3', 'cat', f'/x/{p.name}'],
                              capture_output=True, check=True).stdout


def read_any(path):
    return read_bytes_any(path).decode('utf-8', 'surrogateescape')


def parse_env(text):
    for line in text.splitlines():
        m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
        if m:
            k, v = m.groups()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            yield k, v


class Vault:
    """Placeholder name <-> real value, kept stable by processing files in sorted order."""

    def __init__(self):
        self.names, self.by_value = {}, {}

    def add(self, value, hint, kind='secret'):
        """Name a value after where it sits, not what it is, so a reused password does not
        show up as one placeholder in several places. by_value keeps the first name for the
        catch-all pass over text that has no key to name it by."""
        base = re.sub(r'[^A-Z0-9]+', '_', hint.upper()).strip('_') or 'VALUE'
        name, n = base, 1
        while name in self.names and self.names[name]['value'] != value:
            n += 1
            name = f'{base}_{n}'
        self.names[name] = {'value': value, 'kind': kind}
        self.by_value.setdefault((kind, value), name)
        return name

    def token(self, value, hint, kind='secret'):
        return f'[[{kind}:{self.add(value, hint, kind)}]]'

    def unseal(self, text):
        def rep(m):
            if m.group(2) not in self.names:
                raise KeyError(f'no value for placeholder {m.group(0)}')
            return self.names[m.group(2)]['value']
        return PH.sub(rep, text)


# ---------------------------------------------------------------- redaction
def extract(text, rel, vault):
    """First pass: replace values sitting under secret-looking keys, URL passwords, tokens."""
    app, suffix = app_of(rel), Path(rel).suffix
    if '/plugins/configurations/' in rel:
        p = Path(rel)
        app = 'JELLYFIN_' + (p.stem if p.parent.name == 'configurations' else p.parent.name).split('.')[-1]

    def kv(m):
        k, v = m.group('k'), m.group('v')
        if not usable(v):
            return m.group(0)
        if secret_key(k):
            tok = vault.token(v, f'{app}_{re.split(r"[^A-Za-z0-9_]", k)[-1]}')
        elif IDENTITY_KEY.match(re.split(r'[\\/.]', k)[-1]):
            tok = vault.token(v, f'{app}_{k}', 'private')
        else:
            return m.group(0)
        s, e = m.start('v') - m.start(0), m.end('v') - m.start(0)
        return m.group(0)[:s] + tok + m.group(0)[e:]

    text = XML_EL.sub(kv, text)
    text = JSON_KV.sub(kv, text)
    if suffix not in CODE_SUFFIXES and suffix != '.json':
        text = KV_LINE.sub(kv, text)
    elif suffix in CODE_SUFFIXES:   # code: only quoted literals that look like real secrets
        text = KV_LINE.sub(lambda m: kv(m) if m.group('q') and looks_random(m.group('v')) else m.group(0), text)
    text = URL_CRED.sub(lambda m: m.group(0) if not usable(m.group('v')) else
                        m.group('s') + m.group('u') + ':' + vault.token(m.group('v'), f'{app}_URL_PASSWORD') + '@', text)
    text = QUERY_TOK.sub(lambda m: m.group(0) if not usable(m.group('v')) else
                         m.group('pre') + vault.token(m.group('v'), f'{app}_URL_TOKEN'), text)
    for rx in CUSTOM:
        text = rx.sub(lambda m: m.group(0).replace(m.group('v'), vault.token(m.group('v'), f'{app}_TOKEN')), text)
    return text


def outside_placeholders(text, fn):
    parts = PH.split(text)     # [text, kind, name, text, kind, name, ...]
    out = []
    for i in range(0, len(parts), 3):
        out.append(fn(parts[i]))
        if i + 2 < len(parts):
            out.append(f'[[{parts[i + 1]}:{parts[i + 2]}]]')
    return ''.join(out)


def boundary(term):
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', re.I)


def redact(text, vault, known, terms):
    """Second pass: every known secret value anywhere, then personal terms, e-mails, UPI ids."""
    def plain(seg):
        for value, name in known:
            if value in seg:
                seg = seg.replace(value, f'[[secret:{name}]]')
        return seg
    text = outside_placeholders(text, plain)

    def pii(seg):
        seg = by_terms(seg, [t for t in terms if '@' in t[1]])
        seg = EMAIL.sub(lambda m: m.group(0) if EMAIL_OK.search(m.group(0)) else vault.token(
            m.group(0), 'EMAIL_' + hashlib.sha1(m.group(0).lower().encode()).hexdigest()[:6], 'private'), seg)
        seg = by_terms(seg, [t for t in terms if '@' not in t[1]])
        seg = VPA.sub(lambda m: vault.token(
            m.group(0), 'UPI_' + hashlib.sha1(m.group(0).lower().encode()).hexdigest()[:6], 'private'), seg)
        return seg

    def by_terms(seg, terms):
        for name, term, rx in terms:
            def rep(m, name=name, term=term):
                s = m.group(0)
                sfx = ('' if s == term else '_UC' if s == term.upper() else '_LC' if s == term.lower()
                       else '_TC' if s == term.title() else '_V' + hashlib.sha1(s.encode()).hexdigest()[:4].upper())
                vault.names.setdefault(name + sfx, {'value': s, 'kind': 'private'})
                return f'[[private:{name}{sfx}]]'
            seg = rx.sub(rep, seg)
        return seg
    return outside_placeholders(text, pii)


def conf_lines(name):
    p = CONF / name
    return [l.strip() for l in read_text(p).splitlines() if l.strip() and not l.startswith('#')] if p.exists() else []


def load_terms():
    path = CONF / 'private-terms.env'
    terms = [(k, v) for k, v in parse_env(read_text(path))] if path.exists() else []
    known = {v.lower() for k, v in terms}
    for v in sorted(live_names()):
        if v.lower() not in known:
            terms.append(('LIVE_' + hashlib.sha1(v.lower().encode()).hexdigest()[:6].upper(), v))
            known.add(v.lower())
    return [(k, v, boundary(v)) for k, v in sorted(terms, key=lambda t: -len(t[1]))]


def live_names():
    """Names of the people and accounts the apps know about, read fresh on every run, so a new
    user or account is redacted without anyone having to remember to add it."""
    names = set()

    def q(container, user, db, sql):
        r = subprocess.run(['docker', 'exec', container, 'psql', '-U', user, '-d', db, '-Atc', sql],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.splitlines() if r.returncode == 0 else []
    try:
        names.update(q('jellystat-db', 'jellystat', 'jfstat', 'select "Name" from jf_users'))
        names.update(q('finance-db', 'firefly', 'firefly',
                       "select a.name from accounts a join account_types t on t.id = a.account_type_id "
                       "where a.deleted_at is null and t.type in ('Asset account', 'Loan', 'Debt', 'Mortgage', "
                       "'Liability credit account', 'Default account')"))
        names.update(q('paperless-db', 'paperless', 'paperless', 'select name from documents_correspondent'))
    except Exception as e:
        print(f'  live names (databases): {type(e).__name__}')
    try:
        key = json.loads(read_text(LIVE / 'jellyfin/servarr/jellyseerr/settings.json'))['main']['apiKey']
        for u in http_json('http://127.0.0.1:5055/api/v1/user?take=500', {'X-Api-Key': key}).get('results', []):
            names.update(str(u.get(k) or '') for k in ('displayName', 'email', 'username', 'jellyfinUsername', 'plexUsername'))
    except Exception as e:
        print(f'  live names (seerr): {type(e).__name__}')
    try:
        person = json.loads(read_text(LIVE / 'homeassistant/config/.storage/person'))
        names.update(i.get('name', '') for i in person.get('data', {}).get('items', []))
    except Exception as e:
        print(f'  live names (home assistant): {type(e).__name__}')
    try:
        ts = json.loads(subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=15).stdout)
        for u in (ts.get('User') or {}).values():
            names.update([u.get('LoginName', ''), u.get('DisplayName', '')])
    except Exception as e:
        print(f'  live names (tailscale): {type(e).__name__}')
    out = set()
    for n in names:
        n = ' '.join(n.split())
        if len(n) >= 4 and n.lower() not in STOP_TERMS and n.lower() not in PUBLIC_VALUES:
            out.add(n)
    return out


# ---------------------------------------------------------------- live exports (settings kept in app DBs)
VOLATILE = {'freeSpace', 'totalSpace', 'unmappedFolders', 'lastExecutionTime', 'lastStartTime',
            'lastDuration', 'RefreshProgress', 'RefreshStatus', 'PrimaryImageItemId', 'ItemId'}


def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def scrub_obj(o, app, vault):
    """Drop volatile counters; blank out arr-style {"name","value"} fields that hold secrets."""
    if isinstance(o, dict):
        o = {k: scrub_obj(v, app, vault) for k, v in o.items() if k not in VOLATILE}
        name, val = o.get('name'), o.get('value')
        if isinstance(name, str) and isinstance(val, str) and usable(val) and (
                secret_key(name) or o.get('privacy') in ('apiKey', 'password')):
            o['value'] = vault.token(val, f'{app}_{name}')
        elif isinstance(name, str) and isinstance(val, str) and usable(val) and o.get('privacy') == 'userName':
            o['value'] = vault.token(val, f'{app}_{name}', 'private')
        return o
    if isinstance(o, list):
        return [scrub_obj(v, app, vault) for v in o]
    return o


def exports(vault):
    """Return ({repo path: json text}, {app: ok}) for settings that only live in app databases."""
    out, status = {}, {}

    def put(app, name, data):
        out[f'exports/{app}/{name}.json'] = json.dumps(scrub_obj(data, app.upper(), vault), indent=2,
                                                      sort_keys=True, ensure_ascii=False) + '\n'

    def run(app, fn):
        try:
            fn()
            status[app] = True
        except Exception as e:   # keep last good export; never fail the whole sync on one app
            status[app] = False
            print(f'  export {app}: skipped ({type(e).__name__}: {str(e)[:120]})')

    def arr(app, port, ver, endpoints):
        key = re.search(r'<ApiKey>([^<]+)</ApiKey>', read_text(LIVE / f'jellyfin/servarr/{app}/config.xml')).group(1)
        for ep in endpoints:
            try:
                data = http_json(f'http://127.0.0.1:{port}/api/{ver}/{ep}', {'X-Api-Key': key})
            except urllib.error.HTTPError as e:
                if e.code == 404:       # endpoint not in this app version
                    continue
                raise
            put(app, ep.replace('/', '-'), data)

    common = ['qualityprofile', 'customformat', 'qualitydefinition', 'config/naming', 'config/mediamanagement',
              'config/indexer', 'config/downloadclient', 'delayprofile', 'releaseprofile', 'downloadclient',
              'indexer', 'notification', 'importlist', 'rootfolder', 'tag', 'remotepathmapping', 'autotagging']
    run('radarr', lambda: arr('radarr', 7878, 'v3', common))
    run('sonarr', lambda: arr('sonarr', 8989, 'v3', common))
    run('prowlarr', lambda: arr('prowlarr', 9696, 'v1', ['indexer', 'applications', 'downloadclient',
                                                         'indexerproxy', 'tag', 'appprofile', 'notification']))

    def seerr():
        key = json.loads(read_text(LIVE / 'jellyfin/servarr/jellyseerr/settings.json'))['main']['apiKey']
        put('seerr', 'discover-sliders', http_json('http://127.0.0.1:5055/api/v1/settings/discover', {'X-Api-Key': key}))
    run('seerr', seerr)

    def bazarr():
        key = re.search(r'(?m)^auth:\n(?:  .*\n)*?  apikey: (\S+)',
                        read_text(LIVE / 'jellyfin/servarr/bazarr/config/config.yaml')).group(1).strip('"\'')
        put('bazarr', 'language-profiles', http_json('http://127.0.0.1:6767/api/system/languages/profiles', {'X-API-KEY': key}))
    run('bazarr', bazarr)

    def jellyfin():
        key = dict(parse_env(read_text(LIVE / 'jellyfin/servarr/scripts/prewarm.env')))['JF_KEY']
        h = {'Authorization': f'MediaBrowser Token="{key}"'}   # legacy X-Emby-Token is off
        put('jellyfin', 'plugins', [{k: p.get(k) for k in ('Name', 'Version', 'Id', 'Status')}
                                    for p in http_json('http://127.0.0.1:8096/Plugins', h)])
        put('jellyfin', 'libraries', http_json('http://127.0.0.1:8096/Library/VirtualFolders', h))
    run('jellyfin', jellyfin)

    def pihole():
        q = {'adlists': 'select id, address, enabled, comment, type from adlist order by id',
             'domains': 'select id, type, domain, enabled, comment from domainlist order by id',
             'groups': 'select id, enabled, name, description from "group" order by id',
             'clients': 'select id, ip, comment from client order by id'}
        for name, sql in q.items():
            r = subprocess.run(['docker', 'exec', 'pihole', 'pihole-FTL', 'sqlite3', '-json',
                                '/etc/pihole/gravity.db', sql], capture_output=True, text=True, check=True)
            put('pihole', name, json.loads(r.stdout or '[]'))
    run('pihole', pihole)

    def grafana():
        env = dict(parse_env(read_text(LIVE / 'backup.env')))
        auth = base64.b64encode(f"{env['GRAFANA_USER']}:{env['GRAFANA_PASS']}".encode()).decode()
        h = {'Authorization': f'Basic {auth}'}
        for name, ep in [('datasources', 'datasources'), ('folders', 'folders'),
                         ('alert-rules', 'v1/provisioning/alert-rules'),
                         ('contact-points', 'v1/provisioning/contact-points'),
                         ('notification-policies', 'v1/provisioning/policies')]:
            put('grafana', name, http_json(f'http://127.0.0.1:3001/api/{ep}', h))
    run('grafana', grafana)

    def tunnel():
        r = subprocess.run(['docker', 'logs', 'cloudflared_tunnel'], capture_output=True, text=True)
        logs = r.stdout + r.stderr
        lines = [l for l in logs.splitlines() if 'Updated to new configuration' in l]
        m = re.search(r'config="(.*)" version=(\d+)', lines[-1])
        cfg = json.loads(m.group(1).encode().decode('unicode_escape'))
        put('cloudflared', 'tunnel-ingress', {'version': int(m.group(2)), 'ingress': cfg.get('ingress', []),
                                              'warp-routing': cfg.get('warp-routing')})
    run('cloudflared', tunnel)
    return out, status


# ---------------------------------------------------------------- collect + build
def walk_live():
    for root, dirs, files in os.walk(LIVE):
        rel_root = os.path.relpath(root, LIVE)
        rel_root = '' if rel_root == '.' else rel_root + '/'
        dirs[:] = sorted(d for d in dirs if d not in PRUNE_NAMES and rel_root + d not in PRUNE)
        for f in sorted(files):
            rel = rel_root + f
            if not is_skipped(rel) and (LIVE / rel).is_file():
                yield rel


def build():
    """Everything the repo and the bundles should contain, computed in memory."""
    vault, terms = Vault(), load_terms()
    public, private, originals, modes = {}, [], {}, {}
    for rel in walk_live():
        if is_private(rel):
            private.append(rel)
        elif is_public(rel):
            originals[f'docker/{rel}'] = read_any(LIVE / rel)
            modes[f'docker/{rel}'] = (LIVE / rel).stat().st_mode & 0o777
    for src, dest in HOST:
        p = Path(os.path.expanduser(src))
        if p.exists():
            originals[dest] = read_any(p)
            modes[dest] = p.stat().st_mode & 0o777
    for cmd, dest in [(['crontab', '-l'], 'host/crontab.txt'), (['apt-mark', 'showmanual'], 'host/packages.txt')]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            originals[dest], modes[dest] = r.stdout, 0o644

    # secret values from every env-style private file come first, so their names are the env keys
    env_keys = collections.Counter()
    env_files = [r for r in private if r.endswith('.env') or r == 'homeassistant/config/secrets.yaml']
    parsed = {}
    for rel in env_files:
        text = read_any(LIVE / rel)
        kv = list(parse_env(text)) if rel.endswith('.env') else \
            [m.groups() for m in re.finditer(r'(?m)^([A-Za-z_]\w*):\s*["\']?(.*?)["\']?\s*$', text)]
        parsed[rel] = kv
        env_keys.update({k for k, v in kv})
    envname = {}
    for rel in env_files:
        for k, v in parsed[rel]:
            stem = Path(rel).stem.lstrip('.')
            envname[(rel, k)] = k if env_keys[k] == 1 else f'{stack_of(rel)}_{"" if stem == "env" else stem + "_"}{k}'
            if usable(v) and (secret_key(k) or looks_random(v) or URL_CRED.search(v)):
                vault.add(v, envname[(rel, k)])
                if secret_key(k) and ',' in v:      # lists of passwords: each one on its own too
                    for i, part in enumerate(x.strip() for x in v.split(',')):
                        if usable(part) and len(part) >= 5:
                            vault.add(part, f'{k}_{i + 1}')

    exported, export_status = exports(vault)
    stage = {}
    for dest in sorted(originals):
        stage[dest] = extract(originals[dest], dest.removeprefix('docker/'), vault)
    for dest, text in sorted(exported.items()):
        originals[dest] = vault.unseal(text)      # JSON as fetched, used for the round-trip check
        stage[dest] = extract(text, dest, vault)
        modes[dest] = 0o644
    known = sorted(((d['value'], n) for n, d in vault.names.items()
                    if d['kind'] == 'secret' and len(d['value']) >= 6), key=lambda t: -len(t[0]))
    for dest in sorted(stage):
        public[dest] = redact(stage[dest], vault, known, terms)
    for rel in env_files:     # documented, value-free copies of every env file
        if rel.endswith('.env'):
            text = read_any(LIVE / rel)
            lines = []
            for line in text.splitlines(keepends=True):
                if not re.match(r'^\s*(export\s+)?[A-Za-z_]', line):   # comments in private files stay private
                    continue
                m = re.match(r'^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*)$', line, re.S)
                v = m.group(4).strip('"\'') if m else ''
                if m and usable(v) and ('secret', v) in vault.by_value:
                    line = f'{m.group(1)}{m.group(2)}{m.group(3)}{vault.token(v, envname[(rel, m.group(2))])}{m.group(5)}'
                elif m and not SAFE_ENV_VALUE.fullmatch(v):
                    line = f'{m.group(1)}{m.group(2)}{m.group(3)}{vault.token(v, envname[(rel, m.group(2))], "private")}{m.group(5)}'
                lines.append(line)
            public[f'docker/{rel}.example'] = redact(''.join(lines), vault, known, terms)
            modes[f'docker/{rel}.example'] = 0o644
    return dict(vault=vault, terms=terms, public=public, originals=originals, private=private,
                modes=modes, export_status=export_status, known=known)


def roundtrip(b):
    """Every redacted file must turn back into exactly the live file."""
    bad = []
    for dest, text in b['public'].items():
        if dest in b['originals'] and b['vault'].unseal(text) != b['originals'][dest]:
            bad.append(dest)
    return bad


def stack_of(rel):
    return rel.split('/')[0].upper().replace('-', '_') if '/' in rel else 'ROOT'


def make_tar(members):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as t:
        for name in sorted(members):
            data, mode = members[name]
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.mtime = len(data), mode, 0
            t.addfile(ti, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def bundles(b):
    groups = collections.defaultdict(dict)
    for rel in b['private']:
        p = LIVE / rel
        groups[stack_of(rel)][f'docker/{rel}'] = (read_bytes_any(p), p.stat().st_mode & 0o777)
    for src in conf_lines('host-private.txt'):
        p = Path(os.path.expanduser(src))
        if p.exists():
            groups['HOST'][f'host/{p.relative_to(HOME) if p.is_relative_to(HOME) else p}'] = \
                (read_bytes_any(p), p.stat().st_mode & 0o777)
    values = {'values': b['vault'].names, 'modes': {d: m for d, m in b['modes'].items() if d in b['public']}}
    groups['VALUES']['values.json'] = (json.dumps(values, indent=1, sort_keys=True).encode(), 0o600)
    return {name: make_tar(members) for name, members in groups.items()}


# ---------------------------------------------------------------- leak checks
def repo_files():
    """Every file git would publish: tracked plus untracked-but-not-ignored."""
    out = git('ls-files', '-z', '--cached', '--others', '--exclude-standard').stdout
    for rel in sorted(set(filter(None, out.split('\0')))):
        if (REPO / rel).is_file():
            yield REPO / rel


def luhn(number):
    digits = [int(c) for c in number if c.isdigit()][::-1]
    return sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(digits)) % 10 == 0


def gate(b):
    problems, blocked = [], [re.compile(x, re.I) for x in conf_lines('blocked.txt')]
    secrets = {d['value']: n for n, d in b['vault'].names.items() if d['kind'] == 'secret' and len(d['value']) >= 5}
    for rel in b['private']:    # every secret-looking value in the private env files too
        if rel.endswith('.env'):
            for k, v in parse_env(read_any(LIVE / rel)):
                if usable(v) and len(v) >= 5 and (secret_key(k) or looks_random(v)):
                    secrets.setdefault(v, k)
    for path in repo_files():
        rel = str(path.relative_to(REPO))
        text = read_text(path)
        line = lambda i: text.count('\n', 0, i) + 1
        if not rel.endswith('.example') and (is_private(rel.removeprefix('docker/'))
                                             or re.search(r'(^|/)[^/]*\.env$', rel)):
            problems.append(f'{rel}: private file in the repo')
        for value, name in secrets.items():
            i = text.find(value)
            if i >= 0:
                problems.append(f'{rel}:{line(i)}: secret value of {name}')
        for name, term, rx in b['terms']:
            if rx.search(rel):
                problems.append(f'{rel}: path contains {name}')
            for m in rx.finditer(text):
                problems.append(f'{rel}:{line(m.start())}: personal term {name}')
        for m in EMAIL.finditer(text):
            if not EMAIL_OK.search(m.group(0)):
                problems.append(f'{rel}:{line(m.start())}: e-mail address')
        bare = PH.sub(lambda m: ' ' * len(m.group(0)), text)
        if not rel.startswith('tools/'):
            for m in RANDOM_VALUE.finditer(bare):
                tok = m.group(0)
                if re.fullmatch(CAMEL, tok):
                    continue        # CamelCase identifier, e.g. a setting name
                if bare[max(0, m.start() - 2):m.start()] in ('</',) or bare[m.start() - 1:m.start()] == '<':
                    if bare[m.end():m.end() + 1] in ('>', ' ', '/'):
                        continue    # an XML tag name
                ls = bare.rfind('\n', 0, m.start()) + 1
                k = KEY_BEFORE.search(bare[ls:m.start()])
                key = k.group(1) if k else ''
                if not any(re.search(pr, rel) and re.search(kr, key) for pr, kr in RANDOM_OK):
                    problems.append(f'{rel}:{line(m.start())}: unreviewed random-looking value under "{key}"')
        for kind, rx in [('phone number', PHONE), ('card number', CARD), ('Aadhaar number', AADHAAR),
                         ('PAN', PAN), ('IFSC code', IFSC), ('coordinates', GEO)]:
            for m in rx.finditer(bare):
                if kind == 'card number' and not luhn(m.group(0)):
                    continue
                problems.append(f'{rel}:{line(m.start())}: {kind}')
        m = PRIVATE_BLOCK.search(text)
        if m:
            problems.append(f'{rel}:{line(m.start())}: private key material')
        for rx in blocked:
            m = rx.search(text)
            if m:
                problems.append(f'{rel}:{line(m.start())}: blocked word (blocked.txt: {rx.pattern})')
    with tempfile.NamedTemporaryFile('w', suffix='.txt') as ex:
        ex.write('(^|/)\\.git/\n')
        ex.flush()
        r = subprocess.run(['trufflehog', 'filesystem', str(REPO), '--no-verification', '--no-update', '--json',
                            '--exclude-paths', ex.name], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f'trufflehog failed: {r.stderr.strip()[:200]}')
        for l in r.stdout.splitlines():
            try:
                f = json.loads(l)
            except ValueError:
                continue
            d = f.get('SourceMetadata', {}).get('Data', {}).get('Filesystem', {})
            problems.append(f"{os.path.relpath(d.get('file', '?'), REPO)}:{d.get('line', '?')}: trufflehog {f.get('DetectorName')}")
    with tempfile.NamedTemporaryFile(suffix='.json') as rep:
        r = subprocess.run(['gitleaks', 'dir', str(REPO), '--no-banner', '--redact', '--exit-code', '3',
                            '-c', str(REPO / '.gitleaks.toml'), '-f', 'json', '-r', rep.name, '--log-level', 'error'],
                           capture_output=True, text=True)
        if r.returncode == 3:
            for f in json.load(open(rep.name)):
                problems.append(f"{os.path.relpath(f['File'], REPO)}:{f['StartLine']}: gitleaks {f['RuleID']}")
        elif r.returncode != 0:
            problems.append(f'gitleaks failed: {r.stderr.strip()[:200]}')
    return problems


# ---------------------------------------------------------------- write, push, upload
def write_repo(b):
    changed = []
    for dest, text in sorted(b['public'].items()):
        p = REPO / dest
        if not p.exists() or read_text(p) != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
                f.write(text)
            changed.append(dest)
        os.chmod(p, 0o755 if b['modes'].get(dest, 0o644) & 0o100 else 0o644)
    keep = set(b['public'])
    for top in MANAGED:
        for p in (REPO / top).rglob('*') if (REPO / top).exists() else []:
            if p.is_file() and p.name != DOCS and str(p.relative_to(REPO)) not in keep:
                p.unlink()
                changed.append(f'-{p.relative_to(REPO)}')
    for app, ok in b['export_status'].items():   # only prune exports of apps that answered
        d = REPO / 'exports' / app
        if ok and d.exists():
            for p in d.iterdir():
                if str(p.relative_to(REPO)) not in keep:
                    p.unlink()
                    changed.append(f'-{p.relative_to(REPO)}')
    for d in sorted((p for p in REPO.rglob('*') if p.is_dir() and '.git' not in p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    write_inventory()
    return changed


def write_inventory():
    readme = REPO / 'README.md'
    if not readme.exists():
        return
    rows = []
    r = subprocess.run(['docker', 'ps', '-a', '--format', '{{json .}}'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        c = json.loads(line)
        labels = dict(x.split('=', 1) for x in c.get('Labels', '').split(',') if '=' in x)
        cfg = labels.get('com.docker.compose.project.config_files', '')
        stack = os.path.relpath(os.path.dirname(cfg), LIVE) if cfg.startswith(str(LIVE)) else '-'
        ports = sorted({p.split('->')[0].split(':')[-1] for p in c.get('Ports', '').split(', ') if '->' in p},
                       key=lambda x: (len(x), x))
        rows.append((stack, c['Names'], c['Image'], ', '.join(ports) or '-'))
    table = ['| Stack (`docker/…`) | Container | Image | Host ports |', '|---|---|---|---|']
    table += [f'| {s} | {n} | `{i}` | {p} |' for s, n, i, p in sorted(rows)]
    text = read_text(readme)
    new = re.sub(r'(<!-- inventory:start -->\n).*?(<!-- inventory:end -->)',
                 lambda m: m.group(1) + '\n'.join(table) + '\n' + m.group(2), text, flags=re.S)
    if new != text:
        readme.write_text(new)


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, text=True, check=check)


def upload(blobs):
    state_path = CONF / 'state.json'
    state = json.loads(read_text(state_path)) if state_path.exists() else {}
    existing = {s['name'] for s in json.loads(subprocess.run(
        ['gh', 'secret', 'list', '-R', GH_REPO, '--json', 'name'], capture_output=True, text=True, check=True).stdout)}
    updated = []
    for name, blob in sorted(blobs.items()):
        digest = hashlib.sha256(blob).hexdigest()
        want = [f'BUNDLE_{name}_{i:02d}' for i in range(1, 99)]
        if state.get(name, {}).get('sha256') != digest or not all(
                n in existing for n in want[:state.get(name, {}).get('chunks', 0)]):
            enc = subprocess.run(['age', '-R', str(CONF / 'recipients.txt')], input=blob,
                                 capture_output=True, check=True).stdout
            b64 = base64.b64encode(enc).decode()
            chunks = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
            for i, c in enumerate(chunks):
                subprocess.run(['gh', 'secret', 'set', want[i], '-R', GH_REPO], input=c, text=True,
                               capture_output=True, check=True)
            state[name] = {'sha256': digest, 'chunks': len(chunks)}
            updated.append(f'{name}({len(chunks)})')
        for n in existing:   # drop chunks left over from a bigger bundle
            m = re.fullmatch(rf'BUNDLE_{name}_(\d\d)', n)
            if m and int(m.group(1)) > state[name]['chunks']:
                subprocess.run(['gh', 'secret', 'delete', n, '-R', GH_REPO], capture_output=True, check=True)
    for n in existing:      # bundles of stacks that no longer exist
        m = re.fullmatch(r'BUNDLE_(.+)_\d\d', n)
        if m and m.group(1) not in blobs:
            subprocess.run(['gh', 'secret', 'delete', n, '-R', GH_REPO], capture_output=True, check=True)
            state.pop(m.group(1), None)
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
    return updated


def cmd_sync(args):
    b = build()
    bad = roundtrip(b)
    if bad:
        sys.exit(f'round-trip check failed (redaction is not reversible) for: {", ".join(bad[:10])}')
    changed = write_repo(b)
    problems = gate(b)
    print(f'{len(b["public"])} public files ({len(changed)} changed), {len(b["private"])} private files, '
          f'{len(b["vault"].names)} placeholders; exports: '
          + ' '.join(f'{a}={"ok" if ok else "FAILED"}' for a, ok in b['export_status'].items()))
    if problems:
        print('LEAK CHECK FAILED - nothing was committed or pushed:\n  ' + '\n  '.join(problems[:60]))
        sys.exit(2)
    print('leak checks passed')
    if not args.push:
        return
    git('add', '-A')
    if git('diff', '--cached', '--quiet', check=False).returncode:
        areas = sorted({'/'.join(c.lstrip('-').split('/')[:2]) for c in changed}) or ['README']
        git('commit', '-q', '-m', f'Sync from live: {", ".join(areas)[:200]}')
        print('committed', git('rev-parse', '--short', 'HEAD').stdout.strip())
    git('push', '-q', '-u', 'origin', 'main')
    print('bundles uploaded:', ', '.join(upload(bundles(b))) or 'none changed')


def cmd_check(args):
    b = build()
    problems = gate(b) + [f'{d}: not reversible' for d in roundtrip(b)]
    print('\n'.join(problems) or 'leak checks passed')
    sys.exit(2 if problems else 0)


def cmd_restore(args):
    target, tmp = Path(args.target), Path(tempfile.mkdtemp())
    for f in sorted(Path(args.bundles).glob('*.tar.gz.age')):
        plain = subprocess.run(['age', '-d', '-i', args.identity, str(f)], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(plain), mode='r:gz') as t:
            t.extractall(tmp, filter='data')
    data = json.loads(read_text(tmp / 'values.json'))
    vault = Vault()
    vault.names = data['values']
    n = 0
    for top in ('docker', 'host', 'apps'):
        for src in sorted((REPO / top).rglob('*')):
            rel = src.relative_to(REPO)
            if not src.is_file() or src.name.endswith('.example') or src.name == DOCS:
                continue
            dest = target / rel.relative_to('docker') if top == 'docker' else target / '_host' / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
                f.write(vault.unseal(read_text(src)))
            os.chmod(dest, data['modes'].get(str(rel), 0o644))
            n += 1
    for top, base in (('docker', target), ('host', target / '_host' / 'home')):
        for src in sorted((tmp / top).rglob('*')) if (tmp / top).exists() else []:
            if src.is_file():
                dest = base / src.relative_to(tmp / top)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                n += 1
    shutil.rmtree(tmp)
    print(f'restored {n} files into {target} (host files under {target / "_host"}; copy those by hand with sudo)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('sync')
    s.add_argument('--push', action='store_true')
    sub.add_parser('check')
    r = sub.add_parser('restore')
    r.add_argument('bundles')
    r.add_argument('--identity', required=True)
    r.add_argument('--target', default=str(LIVE))
    args = ap.parse_args()
    {'sync': cmd_sync, 'check': cmd_check, 'restore': cmd_restore}[args.cmd](args)


if __name__ == '__main__':
    main()
