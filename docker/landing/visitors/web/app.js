/* Visitors dashboard - renders /api/data (server.py). No libraries and no inline styles
   (the CSP forbids them); every value that came from a visitor goes in via textContent. */
'use strict';
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const TZ = 'Asia/Kolkata';
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- DOM helpers
  function build(e, attrs, kids) {
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'text') e.textContent = v;
      else if (k === 'class') e.setAttribute('class', v);
      else e.setAttribute(k, v === true ? '' : String(v));
    }
    for (const k of kids.flat()) if (k != null && k !== false) e.append(k);
    return e;
  }
  const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
  const s = (tag, attrs, ...kids) => build(document.createElementNS(NS, tag), attrs, kids);
  const clear = (e) => { while (e.firstChild) e.firstChild.remove(); return e; };

  // ---------------------------------------------------------------- formatting
  const fWhen = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
  const fDay = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' });
  const fDayLong = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short' });
  const fTime = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
  const when = (iso) => fWhen.format(new Date(iso));
  const isoDay = (d) => new Date(`${d}T12:00:00+05:30`);
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  function ago(iso) {
    const sec = (Date.now() - Date.parse(iso)) / 1000;
    if (sec < 90) return 'just now';
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    if (sec < 7 * 86400) return `${Math.round(sec / 86400)} d ago`;
    return fDay.format(new Date(iso));
  }
  const span = (sec) => (sec == null ? '—' : sec < 60 ? '<1 min' : sec < 3600 ? `${Math.floor(sec / 60)} min` : `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`);
  const RANGE = { 7: 'the last 7 days', 30: 'the last 30 days', 90: 'the last 90 days', all: 'all time' };

  function statusIcon(kind) {
    const svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
    if (kind === 'warn') {
      svg.append(s('path', { d: 'M8 1.8 14.8 13.8H1.2z', class: 'f-warn', 'stroke-linejoin': 'round' }),
        s('path', { d: 'M8 6.3v3.3M8 11.6v.1', fill: 'none', stroke: '#0b0b0b', 'stroke-width': 1.8, 'stroke-linecap': 'round' }));
    } else {
      svg.append(s('circle', { cx: 8, cy: 8, r: 7, class: 'f-good' }),
        s('path', { d: 'M5 8.3l2 2 4-4.3', fill: 'none', stroke: '#fff', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    }
    return svg;
  }
  const badge = (cls, text) => h('span', { class: `badge ${cls}`, text });
  const ownerBadge = (mine) => (mine ? badge('mine dot', 'Yours') : badge('unknown dot', 'Unknown'));

  // ---------------------------------------------------------------- tooltip
  const tip = $('tip');
  function showTip(x, y, head, rows) {
    clear(tip);
    if (head) tip.append(h('div', { class: 'tt-h', text: head }));
    for (const r of rows) {
      tip.append(h('div', { class: 'tt-r' }, r.key ? h('i', { class: `key ${r.key}` }) : null,
        h('strong', { text: r.value }), r.label ? h('span', { text: r.label }) : null));
    }
    tip.hidden = false;
    const w = tip.offsetWidth, ht = tip.offsetHeight, vw = window.innerWidth;
    let left = x + 14, top = y - ht - 12;
    if (left + w > vw - 8) left = x - w - 14;
    if (left < 8) left = 8;
    if (top < 8) top = y + 18;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  const hideTip = () => { tip.hidden = true; };
  window.addEventListener('scroll', hideTip, { passive: true });

  // ---------------------------------------------------------------- chart plumbing
  const charts = new Map();
  const ro = new ResizeObserver((entries) => {
    for (const e of entries) {
      const c = charts.get(e.target);
      if (c && Math.abs(e.contentRect.width - c.w) > 1) { c.w = e.contentRect.width; c.draw(); }
    }
  });
  function mount(el, draw) {
    const c = { w: el.clientWidth, draw: () => { clear(el); draw(el, el.clientWidth); } };
    if (!charts.has(el)) ro.observe(el);
    charts.set(el, c);
    c.draw();
  }
  function intTicks(max) {
    const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
    const step = steps.find((st) => max / st <= 4) || 10000;
    const out = [];
    for (let v = 0; v <= Math.ceil(max / step) * step; v += step) out.push(v);
    if (out.length < 2) out.push(step);
    return out;
  }
  // Bars and columns: 4px rounded data end, square at the baseline.
  function hbar(x, y, w, ht) {
    const r = Math.min(4, w / 2, ht / 2);
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + ht - r}Q${x + w},${y + ht} ${x + w - r},${y + ht}H${x}Z`;
  }
  function vcol(x, y, w, ht, round) {
    if (!round) return `M${x},${y}H${x + w}V${y + ht}H${x}Z`;
    const r = Math.min(4, w / 2, ht);
    return `M${x},${y + ht}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + ht}Z`;
  }
  const fit = (text, px) => {
    const n = Math.max(4, Math.floor(px / 7));
    return text.length > n ? `${text.slice(0, n - 1)}…` : text;
  };

  // ---------------------------------------------------------------- API
  async function api(path, body) {
    const opts = body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Visitors': '1' }, body: JSON.stringify(body) }
      : { cache: 'no-store' };
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  function say(id, text, state) {
    const el = $(id);
    el.hidden = !text;
    el.textContent = text || '';
    el.dataset.state = state || '';
  }

  // ---------------------------------------------------------------- state
  let D = null;
  let range = '7';
  let show = 'people';
  let editing = false;
  try { range = localStorage.getItem('range') || '7'; } catch (e) { /* default */ }
  if (!RANGE[range]) range = '7';

  // ---------------------------------------------------------------- banner + tiles
  function renderBanner() {
    const T = D.totals, b = $('banner');
    let kind = 'good', text;
    if (T.unknown_devices) {
      kind = 'warn';
      text = `${plural(T.unknown_devices, 'device')} you haven't marked as yours visited in ${RANGE[range]} `
        + `(${plural(T.unknown_visits, 'visit')}). Mark your own devices below, and block anything you don't recognise.`;
    } else if (T.visits) {
      text = `Only your devices visited in ${RANGE[range]}.`;
    } else {
      text = `No visits in ${RANGE[range]}.`;
    }
    b.hidden = false;
    b.dataset.state = kind;
    clear(b.querySelector('.si')).append(statusIcon(kind));
    b.querySelector('.txt').textContent = text;
  }

  function renderTiles() {
    const T = D.totals;
    const tiles = [
      ['Visits', T.visits, `${T.mine_visits} yours · ${T.unknown_visits} unknown`],
      ['Devices', T.devices, 'with at least one visit'],
      ['Unknown devices', T.unknown_devices, T.unknown_devices ? 'not marked as yours' : 'none', T.unknown_devices ? 'warn' : 'good'],
      ['Blocked attempts', T.attempts, 'turned away'],
      ['Bots & scanners', T.bots + T.probes, `${T.bots} bots · ${T.probes} probes`],
      ['Access requests', T.requests, 'emails left'],
    ];
    clear($('tiles')).append(...tiles.map(([label, value, sub, st]) => h('article', { class: 'tile' },
      h('p', { class: 't-label' }, st ? h('span', { class: 'si', role: 'img', 'aria-label': st === 'warn' ? 'Attention' : 'All clear' }, statusIcon(st)) : null, label),
      h('p', { class: 't-value', text: String(value) }),
      h('p', { class: 't-sub', text: sub }))));
  }

  // ---------------------------------------------------------------- charts
  function renderByDevice() {
    const devs = D.devices.filter((d) => d.visits)
      .sort((a, b) => b.visits - a.visits || Date.parse(b.last) - Date.parse(a.last));
    const top = devs.slice(0, 10);
    $('bydev-sub').textContent = devs.length > 10 ? `Top 10 of ${devs.length} devices, ${RANGE[range]}` : `${RANGE[range][0].toUpperCase()}${RANGE[range].slice(1)}`;
    mount($('bydev'), (el, W) => {
      if (!top.length) { el.append(h('p', { class: 'empty', text: 'No visits in this period.' })); return; }
      const row = 32, bar = 16, H = top.length * row;
      const labW = Math.min(Math.max(W * 0.36, 110), 230);
      const max = Math.max(...top.map((d) => d.visits));
      const x0 = labW + 10, x1 = W - (String(max).length * 8 + 12);
      const X = (v) => x0 + (v / max) * (x1 - x0);
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': `Visits by device, ${RANGE[range]}` });
      top.forEach((d, i) => {
        const y = i * row, by = y + (row - bar) / 2, xe = Math.max(X(d.visits), x0 + 3);
        const band = s('rect', { x: 0, y, width: W, height: row, rx: 6, class: 'hover-band' });
        svg.append(band,
          s('text', { x: 0, y: y + row / 2 + 4, class: 'lbl', text: fit(d.name, labW) }),
          s('path', { d: hbar(x0, by, xe - x0, bar), class: d.mine ? 'f-s1' : 'f-s2' }),
          s('text', { x: xe + 6, y: y + row / 2 + 4, class: 'val', text: String(d.visits) }));
        const hit = s('rect', { x: 0, y, width: W, height: row, class: 'hit' });
        hit.addEventListener('pointermove', (ev) => {
          band.classList.add('on');
          showTip(ev.clientX, ev.clientY, d.name, [
            { key: d.mine ? 's1' : 's2', value: plural(d.visits, 'visit'), label: d.mine ? 'yours' : 'unknown' },
            { value: d.place, label: `last seen ${ago(d.last)}` }]);
        });
        hit.addEventListener('pointerleave', () => { band.classList.remove('on'); hideTip(); });
        hit.addEventListener('click', () => { hideTip(); openDevice(d.key, null); });
        hit.classList.add('clicky');
        svg.append(hit);
      });
      svg.append(s('line', { x1: x0 + 0.5, x2: x0 + 0.5, y1: 0, y2: H, class: 'base' }));
      el.append(svg);
    });
  }

  function renderPerDay() {
    const days = D.days;
    mount($('perday'), (el, W) => {
      const H = +el.dataset.h || 200;
      if (!D.totals.visits) { el.append(h('p', { class: 'empty', text: 'No visits in this period.' })); return; }
      const ticks = intTicks(Math.max(1, ...days.map((d) => d[1] + d[2])));
      const top = ticks[ticks.length - 1];
      const pl = String(top).length * 7 + 12, pr = W - 2, pt = 8, pb = H - 22;
      const slot = (pr - pl) / days.length;
      const cw = Math.max(2, Math.min(24, slot - 2));
      const Y = (v) => pb - (v / top) * (pb - pt);
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': `Visits per day, ${RANGE[range]}` });
      ticks.forEach((t, i) => {
        const y = Math.round(Y(t)) + 0.5;
        svg.append(s('line', { x1: pl, x2: pr, y1: y, y2: y, class: i === 0 ? 'base' : 'gridl' }),
          s('text', { x: pl - 8, y: y + 4, 'text-anchor': 'end', class: 'tick', text: String(t) }));
      });
      days.forEach(([day, mine, unknown], i) => {
        const x = pl + slot * i + (slot - cw) / 2;
        const band = s('rect', { x: pl + slot * i, y: pt, width: slot, height: pb - pt, class: 'hover-band' });
        svg.append(band);
        let yb = pb;
        if (mine) {
          const yt = Y(mine);
          svg.append(s('path', { d: vcol(x, yt, cw, yb - yt, !unknown), class: 'f-s1' }));
          yb = yt - 2;   // 2px surface gap before the next segment
        }
        if (unknown) {
          const yt = Math.min(Y(mine + unknown), yb - 1);
          svg.append(s('path', { d: vcol(x, yt, cw, yb - yt, true), class: 'f-s2' }));
        }
        const hit = s('rect', { x: pl + slot * i, y: 0, width: slot, height: H, class: 'hit' });
        hit.addEventListener('pointermove', (ev) => {
          band.classList.add('on');
          showTip(ev.clientX, ev.clientY, fDayLong.format(isoDay(day)), [
            { key: 's1', value: String(mine), label: 'yours' }, { key: 's2', value: String(unknown), label: 'unknown' }]);
        });
        hit.addEventListener('pointerleave', () => { band.classList.remove('on'); hideTip(); });
        svg.append(hit);
      });
      const idx = days.length > 2 ? [0, Math.floor((days.length - 1) / 2), days.length - 1] : days.map((_, i) => i);
      idx.forEach((i, k) => svg.append(s('text', {
        x: idx.length === 3 ? [pl, (pl + pr) / 2, pr][k] : pl + slot * (i + 0.5), y: H - 4,
        'text-anchor': idx.length === 3 ? ['start', 'middle', 'end'][k] : 'middle', class: 'tick', text: fDay.format(isoDay(days[i][0])),
      })));
      el.append(svg);
    });
    const rows = days.filter((d) => d[1] + d[2]).reverse();
    clear($('perday-table')).append(rows.length
      ? table(['Day', 'Yours', 'Unknown', 'Total'], rows.map(([d, m, u]) => [fDayLong.format(isoDay(d)), m, u, m + u]), [1, 2, 3])
      : h('p', { class: 'empty', text: 'No visits in this period.' }));
  }

  function table(head, rows, numCols) {
    const num = new Set(numCols || []);
    return h('table', { class: 'tbl' },
      h('thead', {}, h('tr', {}, head.map((t, i) => h('th', { class: num.has(i) ? 'num' : null, text: t })))),
      h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c, i) => h('td', { class: num.has(i) ? 'num' : null, text: String(c) }))))));
  }

  // ---------------------------------------------------------------- devices
  function deviceRow(d) {
    const note = [d.name !== d.auto ? d.auto : '', d.source ? `from ${d.source}` : '', d.language,
      d.colo ? `via Cloudflare ${d.colo}` : '', d.cookie ? '' : 'one-off (no device id yet)',
      d.your_network ? 'on a network your devices use' : ''].filter(Boolean).join(' · ');
    const nameCell = h('td', {},
      h('button', { class: 'linkish', type: 'button', title: 'Show everything this device did' }, h('span', { class: 'dev-name', text: d.name })),
      ownerBadge(d.mine), d.blocked ? badge('blocked', 'Blocked') : null,
      note ? h('span', { class: 'sub', text: note }) : null);
    nameCell.querySelector('.linkish').addEventListener('click', (e) => openDevice(d.key, e.currentTarget));
    const placeCell = h('td', {}, h('span', { text: d.place }),
      d.area && d.area.map
        ? h('a', { class: 'sub', href: d.area.map, target: '_blank', rel: 'noopener noreferrer', title: d.area.text, text: 'approx. area ↗' })
        : null);
    const more = d.ips.length - 1;
    const ipCell = h('td', { class: 'mono' }, h('span', { text: d.ip }),
      more > 0 ? h('span', { class: 'sub', title: d.ips.map((p) => p[0]).join('\n'), text: `+${more} more` }) : null);

    const [mineBtn, blockBtn] = deviceActions(d, 'dev-msg');
    const renameBtn = h('button', { class: 'btn small', type: 'button', text: 'Rename' });
    renameBtn.addEventListener('click', () => startRename(nameCell, d));
    const tr = h('tr', { class: 'clickable', 'data-mine': d.mine ? '1' : '0' }, nameCell,
      h('td', { class: 'num' }, h('span', { text: String(d.visits) }),
        range !== 'all' ? h('span', { class: 'sub', text: `${d.visits_all} total` }) : null),
      h('td', { class: 'nowrap', title: `Last page load ${when(d.last)}; last active ${when(d.active)}` }, h('span', { text: ago(d.active) }),
        h('span', { class: 'sub', text: `first ${fDay.format(new Date(d.first))}` })),
      placeCell, ipCell,
      h('td', {}, h('div', { class: 'actions' }, mineBtn, renameBtn, blockBtn)));
    tr.addEventListener('click', (e) => { if (!e.target.closest('button, a, input')) openDevice(d.key, tr); });
    return tr;
  }

  // "This is mine" / "Not mine" and Block / Unblock, for a table row or the device panel.
  function deviceActions(d, msgId) {
    const mineBtn = h('button', { class: 'btn small', type: 'button', text: d.mine ? 'Not mine' : 'This is mine' });
    mineBtn.addEventListener('click', () => act(mineBtn, () => api('/api/device', { key: d.key, mine: !d.mine }), msgId));
    let blockBtn = null;
    if (d.blocked) {
      blockBtn = h('button', { class: 'btn small', type: 'button', text: 'Unblock' });
      blockBtn.addEventListener('click', () => act(blockBtn, () => (d.cookie
        ? api('/api/unblock', { kind: 'device', value: d.key.slice(2) })
        : api('/api/unblock', { kind: 'ip', value: d.ip })), msgId));
    } else if (!d.mine) {
      blockBtn = h('button', { class: 'btn small danger', type: 'button', text: d.cookie ? 'Block' : 'Block IP' });
      blockBtn.addEventListener('click', () => {
        const q = d.cookie
          ? `Block "${d.name}"?\n\nThis browser will see "Not available" instead of the page. A private window or another browser gets a new device id; block its IP from the Blocklist card if it comes back that way.`
          : `Block IP ${d.ip}?\n\nThis visitor never kept a device id, so only its IP can be blocked. Anyone on that IP will see "Not available", including you if it is your home or mobile network.`;
        if (!window.confirm(q)) return;
        act(blockBtn, () => api('/api/block', { kind: 'device', value: d.key }), msgId);
      });
    }
    return [mineBtn, blockBtn];
  }

  function startRename(cell, d) {
    editing = true;
    const input = h('input', { class: 'rename', type: 'text', maxlength: 40, value: d.name === d.auto ? '' : d.name, placeholder: d.auto, 'aria-label': 'Device name' });
    clear(cell).append(input);
    input.focus();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      editing = false;
      if (save) act(null, () => api('/api/device', { key: d.key, name: input.value }));
      else render();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', () => finish(true));
  }

  async function act(button, fn, msgId = 'dev-msg') {
    if (button) button.disabled = true;
    try {
      await fn();
      say(msgId, '');
      await load();
    } catch (e) {
      say(msgId, e.message, 'err');
      if (button) button.disabled = false;
    }
  }

  function renderDevices() {
    if (editing) return;
    const t = $('devices');
    clear(t);
    $('devices-empty').hidden = D.devices.length > 0;
    if (!D.devices.length) return;
    const sorted = [...D.devices].sort((a, b) => (a.mine - b.mine) || Date.parse(b.active) - Date.parse(a.active));
    t.append(h('thead', {}, h('tr', {}, ['Device', 'Visits', 'Last seen', 'Where', 'IP', ''].map((x, i) => h('th', { class: i === 1 ? 'num' : null, text: x })))),
      h('tbody', {}, sorted.map(deviceRow)));
  }

  // ---------------------------------------------------------------- activity
  function renderActivity() {
    document.querySelectorAll('.chips button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.show === show)));
    const t = clear($('activity'));
    let head, rows;
    if (show === 'people' || show === 'unknown') {
      const list = D.visits.filter((v) => show === 'people' || !v.mine);
      head = ['Time (IST)', 'Device', 'Came from', 'Open for', 'Location', 'IP'];
      rows = list.slice(0, 150).map((v) => h('tr', { class: 'clickable', 'data-key': v.key },
        h('td', { class: 'nowrap', text: when(v.when) }),
        h('td', {}, h('span', { text: v.name }), ownerBadge(v.mine), v.name !== v.device ? h('span', { class: 'sub', text: v.device }) : null),
        h('td', { text: v.source }), h('td', { class: 'nowrap', text: span(v.open_s) }),
        h('td', {}, h('span', { text: v.place }), v.colo || v.language ? h('span', { class: 'sub', text: [v.language, v.colo && `via ${v.colo}`].filter(Boolean).join(' · ') }) : null),
        h('td', { class: 'mono', text: v.ip })));
      $('vis-sub').textContent = `${plural(list.length, 'visit')} in ${RANGE[range]}${list.length > 150 ? ', latest 150 shown' : ''}`;
    } else if (show === 'attempts') {
      head = ['Time (IST)', 'Device', 'Location', 'IP', 'Asked for'];
      rows = D.attempts.slice(0, 150).map((r) => h('tr', { class: 'clickable', 'data-key': r.key },
        h('td', { class: 'nowrap', text: when(r.when) }), h('td', { text: r.name }), h('td', { text: r.place }),
        h('td', { class: 'mono', text: r.ip }), h('td', { class: 'mono', text: r.path })));
      $('vis-sub').textContent = `${plural(D.attempts.length, 'blocked attempt')} in ${RANGE[range]}`;
    } else {
      head = ['Time (IST)', 'What', 'Location', 'IP', 'Request'];
      rows = D.others.slice(0, 150).map((r) => h('tr', {},
        h('td', { class: 'nowrap', text: when(r.when) }),
        h('td', {}, h('span', { text: r.bot ? r.label : 'Scanner or broken link' }), r.bot ? null : h('span', { class: 'sub', text: r.label })),
        h('td', { text: r.place }), h('td', { class: 'mono', text: r.ip }),
        h('td', { class: 'mono', text: `${r.status} ${r.path}` })));
      $('vis-sub').textContent = `${plural(D.others.length, 'request')} from bots, link previews and scanners in ${RANGE[range]}`;
    }
    rows.forEach((tr) => { if (tr.dataset.key) tr.addEventListener('click', () => openDevice(tr.dataset.key, tr)); });
    $('activity-empty').hidden = rows.length > 0;
    if (rows.length) t.append(h('thead', {}, h('tr', {}, head.map((x) => h('th', { text: x })))), h('tbody', {}, rows));
  }

  // ---------------------------------------------------------------- requests, blocklist, reports
  function renderRequests() {
    const list = D.requests.filter((r) => r.valid);
    const t = clear($('requests'));
    $('requests-empty').hidden = list.length > 0;
    if (!list.length) return;
    t.append(h('thead', {}, h('tr', {}, ['Time (IST)', 'Email', 'Location', 'Device', 'IP'].map((x) => h('th', { text: x })))),
      h('tbody', {}, list.map((r) => h('tr', {},
        h('td', { class: 'nowrap', text: when(r.when) }), h('td', { text: r.email }), h('td', { text: r.place }),
        h('td', { text: r.device }), h('td', { class: 'mono', text: r.ip })))));
  }

  function renderBlocklist() {
    const ul = clear($('blocklist'));
    $('blocklist-empty').hidden = D.blocklist.length > 0;
    for (const e of [...D.blocklist].reverse()) {
      const btn = h('button', { class: 'btn small', type: 'button', text: 'Unblock' });
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try { await api('/api/unblock', { kind: e.kind, value: e.value }); say('block-msg', `Unblocked ${e.kind === 'ip' ? e.value : 'device'}.`, 'ok'); await load(); } catch (err) { say('block-msg', err.message, 'err'); btn.disabled = false; }
      });
      ul.append(h('li', {},
        h('div', { class: 'what' },
          h('strong', { text: e.kind === 'ip' ? e.value : `device …${e.value.slice(-8)}` }),
          badge('', e.kind === 'ip' ? 'IP' : 'Device id'),
          h('span', { class: 'sub muted', text: `${e.note || ''}${e.note ? ' · ' : ''}since ${fDay.format(new Date(e.at))}` })),
        btn));
    }
  }

  function renderReports() {
    const box = clear($('reports'));
    $('reports-empty').hidden = D.reports.length > 0;
    D.reports.forEach((r, i) => {
      const det = h('details', { class: 'report', open: i === 0 },
        h('summary', {}, h('strong', { text: r.label }),
          h('span', { text: plural(r.visits, 'visit') }), h('span', { text: plural(r.devices, 'device') }),
          r.unknown.length ? badge('unknown dot', `${r.unknown.length} unknown`) : badge('mine dot', 'only yours')));
      const body = h('div', { class: 'report-body' });
      if (r.unknown.length) {
        body.append(h('h3', { text: 'Unknown devices' }),
          h('div', { class: 'table-wrap' }, h('table', { class: 'tbl' },
            h('thead', {}, h('tr', {}, ['Device', 'Visits', 'Where', 'IPs', 'Last seen'].map((x, k) => h('th', { class: k === 1 ? 'num' : null, text: x })))),
            h('tbody', {}, r.unknown.map((d) => h('tr', {},
              h('td', {}, h('span', { text: d.name }), d.blocked ? badge('blocked', 'Blocked') : null,
                d.name !== d.device ? h('span', { class: 'sub', text: d.device }) : null,
                d.your_network ? h('span', { class: 'sub', text: 'on a network your devices use' }) : null),
              h('td', { class: 'num', text: String(d.visits) }),
              h('td', {}, h('span', { text: d.places.join(', ') }),
                d.area && d.area.map ? h('a', { class: 'sub', href: d.area.map, target: '_blank', rel: 'noopener noreferrer', text: 'approx. area ↗' }) : null),
              h('td', { class: 'mono', text: d.ips.join(', ') }),
              h('td', { class: 'nowrap', text: when(d.last) })))))));
      }
      if (r.mine.length) body.append(h('p', { text: `Yours: ${r.mine.map((m) => `${m.name} (${m.visits})`).join(', ')}` }));
      const extra = [r.blocked_attempts && plural(r.blocked_attempts, 'blocked attempt'), r.bots && plural(r.bots, 'bot request'),
        r.probes && plural(r.probes, 'scanner probe'), r.requests.length && plural(r.requests.length, 'access request')].filter(Boolean);
      if (extra.length) body.append(h('p', { class: 'muted', text: `Also: ${extra.join(', ')}.` }));
      if (r.requests.length) body.append(h('p', { text: `Requests: ${r.requests.map((q) => `${q.email} (${q.place})`).join(', ')}` }));
      if (!r.visits && !extra.length) body.append(h('p', { class: 'muted', text: 'No visits that week.' }));
      det.append(body);
      box.append(det);
    });
  }

  // ---------------------------------------------------------------- device panel
  let openKey = null, opener = null;
  const fClock = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  function openDevice(key, from) {
    openKey = key;
    opener = from;
    history.replaceState(null, '', `#device=${encodeURIComponent(key)}`);
    $('drawer').hidden = false;
    document.body.classList.add('no-scroll');
    clear($('dr-body')).append(h('p', { class: 'empty', text: 'Loading…' }));
    $('dr-close').focus();
    refreshDevice();
  }
  function closeDevice() {
    openKey = null;
    history.replaceState(null, '', location.pathname);
    $('drawer').hidden = true;
    document.body.classList.remove('no-scroll');
    if (opener && document.body.contains(opener)) opener.focus();
  }
  async function refreshDevice() {
    if (!openKey) return;
    const key = openKey;
    try {
      const r = await api(`/api/device?key=${encodeURIComponent(key)}`);
      if (key === openKey) renderPanel(r);
    } catch (e) {
      clear($('dr-body')).append(h('p', { class: 'empty', text: e.message }));
    }
  }

  function renderPanel({ device: d, sessions }) {
    clear($('dr-title')).append(...[d.name, ownerBadge(d.mine), d.blocked ? badge('blocked', 'Blocked') : null].filter(Boolean));
    $('dr-sub').textContent = [d.name !== d.auto ? d.auto : '',
      d.cookie ? `device id …${d.key.slice(-6)}` : 'one-off visitor (never kept a device id)'].filter(Boolean).join(' · ');
    const facts = [
      ['First seen', when(d.first)],
      ['Last active', `${when(d.active)} (${ago(d.active)})`],
      ['Visits', `${d.visits_all} in total`],
      ['Came from', d.source || '—'],
      ['Location', h('span', {}, d.place,
        d.area && d.area.map ? h('span', {}, ' · ', h('a', { href: d.area.map, target: '_blank', rel: 'noopener noreferrer', text: `approx. area (${d.area.text}) ↗` })) : null)],
      ['Language', d.language || '—'],
      ['Cloudflare', d.colo ? `${d.colo} data centre` : '—'],
      ['IPs', h('span', { class: 'mono' }, d.ips.map((p, i) => [i ? h('br') : null, `${p[0]}  ·  ${ago(p[1])}`]).flat())],
    ];
    const [mineBtn, blockBtn] = deviceActions(d, 'dr-msg');
    const body = clear($('dr-body'));
    body.append(
      h('dl', { class: 'facts' }, facts.map(([k, v]) => [h('dt', { text: k }), h('dd', {}, v)]).flat()),
      h('div', { class: 'actions' }, mineBtn, blockBtn),
      h('p', { class: 'form-msg', id: 'dr-msg', role: 'status', hidden: true }),
      h('h3', { class: 'mini-h', text: sessions.length ? `Activity · ${sessions.length} session${sessions.length === 1 ? '' : 's'}, newest first` : 'Activity' }));
    if (!sessions.length) body.append(h('p', { class: 'empty', text: 'Nothing logged for this device.' }));
    for (const sess of sessions) {
      const head = `${fDayLong.format(new Date(sess.start))} · ${fTime.format(new Date(sess.start))}–${fTime.format(new Date(sess.end))}`;
      body.append(h('section', { class: 'sess' },
        h('h3', {}, head, h('span', { text: span(sess.duration_s) })),
        h('p', { text: [sess.source, sess.places.join(', '), sess.ips.join(', ')].filter(Boolean).join(' · ') }),
        h('ol', { class: 'tl' }, sess.items.map((it) => h('li', { 'data-kind': it.kind },
          h('time', { datetime: it.t, text: fClock.format(new Date(it.t)) }),
          h('div', {}, h('span', { text: it.text }),
            it.end && it.end !== it.t ? h('span', { class: 'sub', text: `until ${fClock.format(new Date(it.end))}` }) : null,
            it.ip && sess.ips.length > 1 ? h('span', { class: 'sub mono', text: `${it.where} · ${it.ip}` }) : null))))));
    }
  }

  $('dr-close').addEventListener('click', closeDevice);
  $('scrim').addEventListener('click', closeDevice);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openKey) closeDevice(); });

  // ---------------------------------------------------------------- load loop
  function render() {
    for (const f of [renderBanner, renderTiles, renderByDevice, renderPerDay, renderDevices, renderActivity, renderRequests, renderBlocklist, renderReports]) {
      try { f(); } catch (e) { console.error(f.name, e); }
    }
    $('generated').textContent = `Updated ${fTime.format(new Date(D.generated))} IST · refreshes every minute`;
  }
  async function load() {
    try {
      D = await api(`/api/data?range=${encodeURIComponent(range)}`);
      render();
      refreshDevice();
    } catch (e) {
      $('generated').textContent = `Couldn't load: ${e.message}`;
    }
  }

  document.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => {
    range = b.dataset.range;
    document.querySelectorAll('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    try { localStorage.setItem('range', range); } catch (e) { /* not kept */ }
    load();
  }));
  document.querySelectorAll('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.range === range)));
  document.querySelectorAll('.chips button').forEach((b) => b.addEventListener('click', () => { show = b.dataset.show; if (D) renderActivity(); }));
  $('perday-toggle').addEventListener('click', (e) => {
    const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
    e.currentTarget.setAttribute('aria-pressed', String(on));
    e.currentTarget.textContent = on ? 'Chart' : 'Table';
    $('perday').hidden = on;
    $('perday-table').hidden = !on;
  });
  $('block-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('block-ip'), value = input.value.trim();
    if (!value) return;
    if (!window.confirm(`Block ${value}?\n\nAnyone on it will see "Not available", including you if it is your home or mobile network. IPs your marked devices have used are refused.`)) return;
    try {
      const r = await api('/api/block', { kind: 'ip', value });
      say('block-msg', r.added.length ? `Blocked ${r.added.join(', ')}.` : 'Already blocked.', 'ok');
      input.value = '';
      await load();
    } catch (err) {
      say('block-msg', err.message, 'err');
    }
  });

  load();
  const linked = decodeURIComponent(location.hash).match(/^#device=([cu]:[0-9a-f]+)$/);
  if (linked) openDevice(linked[1], null);
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
})();
