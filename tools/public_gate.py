#!/usr/bin/env python3
"""Leak gate for an app mirrored to a PUBLIC repo (called by private_mirrors.sh).

  public_gate.py DIR

Rewrites the personal domain to example.com in DIR's text files, then fails (exit 3) on any
personal term, e-mail address, phone/card/ID number, coordinates, private key or blocked word,
with the same rules as the homelab repo's own gate.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import homelab_sync as h  # noqa: E402

REWRITE = {'DOMAIN': 'example.com'}
LABELS = {'this', 'super', 'return', 'break', 'continue'}   # Kotlin this@Label, not e-mail
# Made-up sample data (owner OK 2026-11-14): skip only the e-mail, phone and card checks there.
SAMPLE_FILES = {'app/web/data/examples.js'}   # resume-web: demo resumes on example.com, fake numbers


def text_files(root):
    for p in sorted(root.rglob('*')):
        if p.is_file() and '.git' not in p.relative_to(root).parts:
            data = p.read_bytes()
            if b'\0' in data:
                continue                    # binary: images, fonts
            try:
                yield p, data.decode()
            except UnicodeDecodeError:
                continue


def main(root):
    terms = h.load_terms()
    blocked = [re.compile(x, re.I) for x in h.conf_lines('blocked.txt')]
    problems = []
    for p, text in text_files(root):
        rel = str(p.relative_to(root))
        new = text
        for name, _, rx in terms:
            if name in REWRITE:
                new = rx.sub(REWRITE[name], new)
        if new != text:
            p.write_text(new)
            text = new
        line = lambda i: text.count('\n', 0, i) + 1
        for name, _, rx in terms:
            if rx.search(rel):
                problems.append(f'{rel}: path contains {name}')
            for m in rx.finditer(text):
                problems.append(f'{rel}:{line(m.start())}: personal term {name}')
        # Minified vendor bundles (pdf.js) hold random e-mail-shaped strings; owner OK 2026-11-14 to skip only this check.
        sample = rel in SAMPLE_FILES
        for m in ([] if sample or rel.endswith(('.min.js', '.min.mjs')) else h.EMAIL.finditer(text)):
            if not h.EMAIL_OK.search(m.group(0)) and m.group(0).split('@')[0] not in LABELS:
                problems.append(f'{rel}:{line(m.start())}: e-mail address')
        for kind, rx in [('phone number', h.PHONE), ('card number', h.CARD), ('Aadhaar number', h.AADHAAR),
                         ('PAN', h.PAN), ('IFSC code', h.IFSC), ('coordinates', h.GEO),
                         ('private key material', h.PRIVATE_BLOCK)]:
            if sample and kind in ('phone number', 'card number'):
                continue
            for m in rx.finditer(text):
                if kind == 'card number' and not h.luhn(m.group(0)):
                    continue
                problems.append(f'{rel}:{line(m.start())}: {kind}')
        for rx in blocked:
            m = rx.search(text)
            if m:
                problems.append(f'{rel}:{line(m.start())}: blocked word')
    for p in problems:
        print('  ' + p)
    return 3 if problems else 0


if __name__ == '__main__':
    sys.exit(main(Path(sys.argv[1])))
