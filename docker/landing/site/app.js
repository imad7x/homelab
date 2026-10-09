/* [[private:DOMAIN]] - renders /api/snapshot.json (written every minute by landing-collector).
   No libraries and no inline styles (the CSP forbids them): colours come from CSS
   classes, and every text value goes in via textContent, never innerHTML. */
'use strict';
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const TZ = 'Asia/Kolkata';
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;

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
  const setText = (id, t) => { const e = $(id); if (e) e.textContent = t; };

  // ---------------------------------------------------------------- formatting
  const en = (d) => new Intl.NumberFormat('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
  const int = (n) => (n == null ? '—' : Math.round(n).toLocaleString('en-US'));
  const compact = (n) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n);
  const fTime = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
  const fDay = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' });
  const fLong = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short' });
  const time = (ms) => fTime.format(ms);
  const isoDay = (d) => new Date(`${d}T12:00:00+05:30`);       // a plain YYYY-MM-DD, read as an IST day
  // ---------------------------------------------------------------- status
  const STATUS_FILL = { good: 'f-good', warn: 'f-warn', serious: 'f-serious', crit: 'f-crit', none: 'f-none' };
  function statusIcon(kind) {
    const svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
    if (kind === 'warn') {
      svg.append(s('path', { d: 'M8 1.8 14.8 13.8H1.2z', class: 'f-warn', 'stroke-linejoin': 'round' }),
        s('path', { d: 'M8 6.3v3.3M8 11.6v.1', fill: 'none', stroke: '#0b0b0b', 'stroke-width': 1.8, 'stroke-linecap': 'round' }));
      return svg;
    }
    const glyph = { good: 'M5 8.3l2 2 4-4.3', crit: 'M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8', none: 'M5 8h6' }[kind];
    svg.append(s('circle', { cx: 8, cy: 8, r: 7, class: STATUS_FILL[kind] }),
      s('path', { d: glyph, fill: 'none', class: kind === 'none' ? 'g-muted' : '', stroke: kind === 'none' ? null : '#fff',
        'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return svg;
  }
  // ---------------------------------------------------------------- tooltip
  const tip = $('tip');
  function showTip(x, y, head, rows) {
    clear(tip);
    if (head) tip.append(h('div', { class: 'tt-h', text: head }));
    for (const r of rows) {
      tip.append(h('div', { class: 'tt-r' },
        r.key ? h('i', { class: r.key[0], 'data-s': r.key[1] }) : null,
        h('strong', { text: r.value }),
        r.label ? h('span', { text: r.label }) : null));
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
  // Every chart redraws at its real pixel width (so 2px lines stay 2px) when its box resizes.
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
  function sweep() {
    for (const el of charts.keys()) if (!document.body.contains(el)) { ro.unobserve(el); charts.delete(el); }
  }
  const redrawAll = () => charts.forEach((c) => c.draw());

  function niceTicks(min, max, n) {
    if (!(max > min)) { max = min + 1; }
    const raw = (max - min) / n, mag = 10 ** Math.floor(Math.log10(raw)), e = raw / mag;
    const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
    const out = [];
    for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const nearest = (xs, x) => {
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - x) < Math.abs(xs[best] - x)) best = i;
    return best;
  };
  const pathOf = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  function endDot(g, x, y, cls) {
    g.append(s('circle', { cx: x, cy: y, r: 6, class: 'f-card' }), s('circle', { cx: x, cy: y, r: 4, class: cls }));
  }

  /* Sparkline: one series, 2px line, 10% wash, end dot, crosshair + tooltip. */
  function sparkline(el, pts, fmt) {
    const data = (pts || []).filter((p) => p[1] != null);
    mount(el, (el, W) => {
      const H = el.clientHeight - 10;
      if (data.length < 2 || W < 30) return;
      const ys = data.map((p) => p[1]);
      let lo = Math.min(...ys), hi = Math.max(...ys);
      const pad = (hi - lo) * 0.15 || 1; lo -= pad; hi += pad;
      const x0 = data[0][0], x1 = data[data.length - 1][0];
      const X = (t) => 2 + ((t - x0) / (x1 - x0)) * (W - 9);
      const Y = (v) => 6 + (1 - (v - lo) / (hi - lo)) * (H - 12);
      const pts2 = data.map((p) => [X(p[0]), Y(p[1])]);
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': `Last 24 hours, now ${fmt(ys[ys.length - 1])}` });
      const line = pathOf(pts2);
      svg.append(s('path', { d: `${line}L${pts2[pts2.length - 1][0]},${H}L${pts2[0][0]},${H}Z`, class: 'area-s1' }),
        s('path', { d: line, class: 'ln s1' }));
      const last = pts2[pts2.length - 1];
      endDot(svg, last[0], last[1], 'f-s1');
      const hover = s('g', { visibility: 'hidden' });
      const xl = s('line', { y1: 0, y2: H, class: 'xhair' });
      const hd = s('g');
      hover.append(xl, hd);
      const hit = s('rect', { x: 0, y: 0, width: W, height: H, class: 'hit' });
      svg.append(hover, hit);
      const xs = pts2.map((p) => p[0]);
      hit.addEventListener('pointermove', (ev) => {
        const r = svg.getBoundingClientRect(), i = nearest(xs, ev.clientX - r.left);
        xl.setAttribute('x1', xs[i]); xl.setAttribute('x2', xs[i]);
        clear(hd); endDot(hd, xs[i], pts2[i][1], 'f-s1');
        hover.setAttribute('visibility', 'visible');
        showTip(ev.clientX, ev.clientY, `${time(data[i][0] * 1000)} IST`, [{ value: fmt(data[i][1]) }]);
      });
      hit.addEventListener('pointerleave', () => { hover.setAttribute('visibility', 'hidden'); hideTip(); });
      el.append(svg);
    });
  }

  /* Line chart with a y axis: one or two series sharing x. series: [{name, cls, pts:[[x,y]]}] */
  function lineChart(el, series, o) {
    const ok = series.filter((x) => x.pts && x.pts.filter((p) => p[1] != null).length > 1);
    mount(el, (el, W) => {
      const H = +el.dataset.h || 140;
      if (!ok.length || W < 60) { if (!ok.length) el.append(h('p', { class: 'empty', text: 'No data yet.' })); return; }
      const all = ok.flatMap((x) => x.pts.filter((p) => p[1] != null).map((p) => p[1]));
      let lo = Math.min(...all), hi = Math.max(...all);
      if (o.zero) lo = 0;
      const span = hi - lo || Math.abs(hi) * 0.1 || 1;
      if (!o.zero) { lo -= span * 0.1; hi += span * 0.1; }
      const ticks = niceTicks(lo, hi, 3);
      const t0 = ticks[0], t1 = ticks[ticks.length - 1];
      const step = ticks.length > 4 ? 2 : 1;
      const labels = ticks.map((t, i) => (i % step ? '' : o.fmtY(t)));
      const gut = Math.max(...labels.map((l) => l.length)) * 6.4 + 10;
      const xs0 = ok[0].pts.map((p) => p[0]);
      const x0 = xs0[0], x1 = xs0[xs0.length - 1];
      const pl = gut, pr = W - 8, pt = 6, pb = H - 20;
      const X = (x) => pl + ((x - x0) / (x1 - x0 || 1)) * (pr - pl);
      const Y = (v) => pt + (1 - (v - t0) / (t1 - t0)) * (pb - pt);
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': o.label || '' });
      ticks.forEach((t, i) => {
        const y = Math.round(Y(t)) + 0.5;
        svg.append(s('line', { x1: pl, x2: pr, y1: y, y2: y, class: i === 0 ? 'base' : 'gridl' }),
          s('text', { x: pl - 8, y: y + 4, 'text-anchor': 'end', class: 'tick', text: labels[i] }));
      });
      const xi = [0, Math.floor((xs0.length - 1) / 2), xs0.length - 1];
      xi.forEach((i, k) => svg.append(s('text', {
        x: X(xs0[i]), y: H - 4, 'text-anchor': ['start', 'middle', 'end'][k], class: 'tick', text: o.fmtX(xs0[i]),
      })));
      const drawn = ok.map((ser) => {
        const p = ser.pts.map((q) => (q[1] == null ? null : [X(q[0]), Y(q[1])]));
        const runs = []; let cur = [];
        p.forEach((q) => { if (q) cur.push(q); else if (cur.length) { runs.push(cur); cur = []; } });
        if (cur.length) runs.push(cur);
        if (ok.length === 1) runs.forEach((r) => svg.append(s('path', { d: `${pathOf(r)}L${r[r.length - 1][0]},${pb}L${r[0][0]},${pb}Z`, class: `area-${ser.cls}` })));
        runs.forEach((r) => svg.append(s('path', { d: pathOf(r), class: `ln ${ser.cls}` })));
        return { ser, p };
      });
      drawn.forEach(({ ser, p }) => {
        const last = [...p].reverse().find(Boolean);
        if (last) endDot(svg, last[0], last[1], `f-${ser.cls}`);
      });
      const hover = s('g', { visibility: 'hidden' });
      const xl = s('line', { y1: pt, y2: pb, class: 'xhair' });
      const hd = s('g');
      hover.append(xl, hd);
      const hit = s('rect', { x: pl, y: 0, width: pr - pl + 8, height: H, class: 'hit' });
      svg.append(hover, hit);
      const xs = xs0.map(X);
      hit.addEventListener('pointermove', (ev) => {
        const r = svg.getBoundingClientRect(), i = nearest(xs, ev.clientX - r.left);
        xl.setAttribute('x1', xs[i]); xl.setAttribute('x2', xs[i]);
        clear(hd);
        const rows = [];
        drawn.forEach(({ ser, p }) => {
          if (p[i]) endDot(hd, p[i][0], p[i][1], `f-${ser.cls}`);
          const v = ser.pts[i] && ser.pts[i][1];
          rows.push({ key: ok.length > 1 ? ['key', ser.cls] : null, value: v == null ? '—' : o.fmtV(v), label: ok.length > 1 ? ser.name : null });
        });
        if (o.extra) rows.push(...o.extra(i));
        hover.setAttribute('visibility', 'visible');
        showTip(ev.clientX, ev.clientY, o.fmtTip(xs0[i]), rows);
      });
      hit.addEventListener('pointerleave', () => { hover.setAttribute('visibility', 'hidden'); hideTip(); });
      el.append(svg);
    });
  }

  /* Stacked columns for the DNS card: allowed (s1) under blocked (s2), 2px gaps. */
  function columns(el, days) {
    mount(el, (el, W) => {
      const H = +el.dataset.h || 150;
      if (!days.length || W < 60) { if (!days.length) el.append(h('p', { class: 'empty', text: 'No data yet.' })); return; }
      const ticks = niceTicks(0, Math.max(...days.map((d) => d.queries)), 3);
      const top = ticks[ticks.length - 1];
      const labels = ticks.map((t) => (t ? compact(t) : '0'));
      const gut = Math.max(...labels.map((l) => l.length)) * 6.4 + 10;
      const pl = gut, pr = W, pt = 6, pb = H - 20;
      const Y = (v) => pt + (1 - v / top) * (pb - pt);
      const slot = (pr - pl) / days.length, cw = Math.max(2, Math.min(24, slot - 2));
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': 'DNS queries per day, allowed and blocked, last 30 days' });
      ticks.forEach((t, i) => {
        const y = Math.round(Y(t)) + 0.5;
        svg.append(s('line', { x1: pl, x2: pr, y1: y, y2: y, class: i === 0 ? 'base' : 'gridl' }),
          s('text', { x: pl - 8, y: y + 4, 'text-anchor': 'end', class: 'tick', text: labels[i] }));
      });
      const cols = [];
      days.forEach((d, i) => {
        const x = pl + slot * i + (slot - cw) / 2, allowed = d.queries - d.blocked;
        const g = s('g', { class: 'col' });
        const ya = Y(allowed), yq = Y(d.queries);
        if (allowed > 0) g.append(s('rect', { x, y: ya, width: cw, height: Math.max(0, pb - ya), class: 'f-s1' }));
        const bTop = yq, bBot = allowed > 0 ? ya - 2 : pb;
        if (bBot - bTop > 0.5) {
          const r = Math.min(4, cw / 2, bBot - bTop);
          g.append(s('path', { class: 'f-s2', d: `M${x},${bBot}V${bTop + r}A${r},${r} 0 0 1 ${x + r},${bTop}H${x + cw - r}A${r},${r} 0 0 1 ${x + cw},${bTop + r}V${bBot}Z` }));
        }
        svg.append(g);
        cols.push(g);
      });
      [0, days.length - 1].forEach((i, k) => svg.append(s('text', {
        x: pl + slot * i + slot / 2, y: H - 4, 'text-anchor': k ? 'end' : 'start', class: 'tick', text: fDay.format(isoDay(days[i].day)),
      })));
      days.forEach((d, i) => {
        const hit = s('rect', { x: pl + slot * i, y: 0, width: slot, height: pb, class: 'hit' });
        hit.addEventListener('pointermove', (ev) => {
          cols.forEach((c, j) => c.setAttribute('opacity', j === i ? 1 : 0.45));
          const share = d.queries ? (100 * d.blocked) / d.queries : 0;
          showTip(ev.clientX, ev.clientY, fLong.format(isoDay(d.day)), [
            { key: ['sw', 's2'], value: int(d.blocked), label: `blocked (${share.toFixed(1)}%)` },
            { key: ['sw', 's1'], value: int(d.queries - d.blocked), label: 'allowed' },
            { value: int(d.queries), label: 'queries' },
          ]);
        });
        hit.addEventListener('pointerleave', () => { cols.forEach((c) => c.removeAttribute('opacity')); hideTip(); });
        svg.append(hit);
      });
      el.append(svg);
    });
  }

  /* Chance of rain per hour: one series on a fixed 0-100 % scale, tooltip per column. */
  function rainBars(el, rain) {
    mount(el, (el, W) => {
      const H = +el.dataset.h || 78;
      if (!rain.length || W < 60) return;
      const gut = 34, pl = gut, pr = W, pt = 4, pb = H - 18;
      const Y = (v) => pt + (1 - v / 100) * (pb - pt);
      const slot = (pr - pl) / rain.length, cw = Math.max(2, Math.min(24, slot - 2));
      const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': 'Chance of rain for each of the next 24 hours' });
      [0, 50, 100].forEach((t, i) => {
        const y = Math.round(Y(t)) + 0.5;
        svg.append(s('line', { x1: pl, x2: pr, y1: y, y2: y, class: i ? 'gridl' : 'base' }),
          s('text', { x: pl - 8, y: y + 4, 'text-anchor': 'end', class: 'tick', text: `${t}%` }));
      });
      const cols = rain.map((r, i) => {
        const x = pl + slot * i + (slot - cw) / 2, y = Y(r[1]), hgt = pb - y;
        let mark;
        if (hgt >= 1) {
          const rr = Math.min(4, cw / 2, hgt);
          mark = s('path', { class: 'f-s1 col', d: `M${x},${pb}V${y + rr}A${rr},${rr} 0 0 1 ${x + rr},${y}H${x + cw - rr}A${rr},${rr} 0 0 1 ${x + cw},${y + rr}V${pb}Z` });
          svg.append(mark);
        }
        return mark;
      });
      [0, Math.floor(rain.length / 2), rain.length - 1].forEach((i, k) => svg.append(s('text', {
        x: pl + slot * i + slot / 2, y: H - 3, 'text-anchor': ['start', 'middle', 'end'][k], class: 'tick', text: rain[i][0],
      })));
      rain.forEach((r, i) => {
        const hit = s('rect', { x: pl + slot * i, y: 0, width: slot, height: pb, class: 'hit' });
        hit.addEventListener('pointermove', (ev) => {
          cols.forEach((c, j) => c && c.setAttribute('opacity', j === i ? 1 : 0.45));
          showTip(ev.clientX, ev.clientY, `${r[0]} IST`, [{ value: `${r[1]}%`, label: r[2] ? `chance · ${r[2]} mm` : 'chance of rain' }]);
        });
        hit.addEventListener('pointerleave', () => { cols.forEach((c) => c && c.removeAttribute('opacity')); hideTip(); });
        svg.append(hit);
      });
      el.append(svg);
    });
  }

  function table(id, head, rows) {
    const el = $(id);
    clear(el).append(h('table', {},
      h('thead', {}, h('tr', {}, head.map((x) => h('th', { scope: 'col', text: x })))),
      h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c, i) => (i ? h('td', { text: c }) : h('th', { scope: 'row', text: c }))))))));
  }
  document.querySelectorAll('.tbl-btn').forEach((b) => {
    b.setAttribute('aria-pressed', 'false');
    b.addEventListener('click', () => {
      const k = b.dataset.for, t = $(`${k}-table`), c = $(`${k}-chart`), show = t.hidden;
      t.hidden = !show; c.hidden = show;
      b.textContent = show ? 'Chart' : 'Table';
      b.setAttribute('aria-pressed', String(show));
    });
  });

  // ---------------------------------------------------------------- weather icons
  const CLOUD_UP = 'M7 14h10a4 4 0 0 0 .4-8A6 6 0 0 0 6 5.5 4.3 4.3 0 0 0 7 14z';
  const WX = {
    sun: ['M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4', 'C12,12,4'],
    moon: ['M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'],
    partly: ['M8 2.5v1.3M3.4 4.4l.9.9M1.8 9h1.3M12.6 4.4l-.9.9M5 11.2a3.2 3.2 0 0 1 5.6-3.4', 'M9.5 20h8a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.4A2.9 2.9 0 0 0 9.5 20z'],
    cloud: ['M7 18h10a4 4 0 0 0 .4-8A6 6 0 0 0 6 9.5 4.3 4.3 0 0 0 7 18z'],
    fog: ['M4 8h16M6 12h12M4 16h16M8 20h8'],
    drizzle: [CLOUD_UP, 'M8 18v.5M12 19v.5M16 18v.5'],
    rain: [CLOUD_UP, 'M8.5 17l-1 3M12.5 17l-1 3M16.5 17l-1 3'],
    snow: [CLOUD_UP, 'M8 18h.01M12 20h.01M16 18h.01'],
    thunder: [CLOUD_UP, 'M12.5 14l-2 3.5h3l-2 3.5'],
  };
  function wxKind(code, day) {
    if (code <= 1) return day ? 'sun' : 'moon';
    if (code === 2) return 'partly';
    if (code === 3) return 'cloud';
    if (code === 45 || code === 48) return 'fog';
    if (code >= 51 && code <= 57) return 'drizzle';
    if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return 'rain';
    if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
    if (code >= 95) return 'thunder';
    return 'cloud';
  }
  function wxIcon(code, day) {
    const svg = s('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
    for (const d of WX[wxKind(code, day)]) {
      if (d.startsWith('C')) { const [cx, cy, r] = d.slice(1).split(','); svg.append(s('circle', { cx, cy, r })); }
      else svg.append(s('path', { d }));
    }
    return svg;
  }

  // ---------------------------------------------------------------- sections
  function renderOverall(S) {
    const pill = $('overall'), svcs = S.services || [];
    const known = svcs.filter((x) => x.up != null), down = svcs.filter((x) => x.up === false);
    const stale = Date.now() - new Date(S.generated) > 5 * 60000;
    let kind, text;
    if (stale) { kind = 'none'; text = 'Status snapshot is out of date'; }
    else if (!known.length) { kind = 'none'; text = 'Status unknown'; }
    else if (!down.length) { kind = 'good'; text = 'All apps online'; }
    else { kind = down.length > 2 ? 'crit' : 'warn'; text = down.length === 1 ? `${down[0].name} is offline` : `${down.length} apps offline`; }
    pill.dataset.state = kind;
    clear(pill.querySelector('.si')).append(statusIcon(kind));
    pill.querySelector('.txt').textContent = text;
  }

  /* Status dot on each app tile; the tiles themselves are static HTML. */
  function renderApps(S) {
    for (const x of S.services || []) {
      const el = document.querySelector(`.app[data-id="${CSS.escape(String(x.id))}"]`);
      if (!el) continue;
      const st = clear(el.querySelector('.app-st'));
      el.dataset.up = x.up === true ? '1' : x.up === false ? '0' : '';
      if (x.up === false) st.append('Offline');
      else st.append(h('span', { class: 'sr', text: x.up ? 'Online' : 'Status unknown' }));
      st.title = x.up === true ? 'Online' : x.up === false ? 'Offline' : 'Status unknown';
    }
  }

  function value(id, num, unit) {
    const e = $(id).querySelector('.t-value');
    clear(e).append(num);
    if (unit) e.append(h('small', { text: unit }));
  }
  function renderVitals(S) {
    const v = S.vitals, t = S.trends || {};
    if (!v) return;
    setText('hero-containers', `${v.containers} containers`);

    // Sub-lines give the 24 h range; no core count or sizes on a public page.
    const range = (series, unit) => {
      const xs = (series || []).map((p) => p[1]).filter((x) => x != null);
      return xs.length ? `24 h: ${Math.round(Math.min(...xs))}–${Math.round(Math.max(...xs))} ${unit}` : '';
    };

    value('t-cpu', en(0).format(v.cpu_pct), '%');
    $('t-cpu').querySelector('.t-sub').textContent = range(t.cpu, '%');
    sparkline($('t-cpu').querySelector('.spark'), t.cpu, (x) => `${en(1).format(x)} %`);

    value('t-mem', en(0).format(v.mem_pct), '%');
    $('t-mem').querySelector('.t-sub').textContent = range(t.mem, '%');
    sparkline($('t-mem').querySelector('.spark'), t.mem, (x) => `${en(1).format(x)} %`);

    value('t-temp', en(0).format(v.temp_c), '°C');
    $('t-temp').querySelector('.t-sub').textContent = range(t.temp, '°C');
    sparkline($('t-temp').querySelector('.spark'), t.temp, (x) => `${en(1).format(x)} °C`);

    if (v.pool_pct != null) {
      const p = v.pool_pct;
      value('t-pool', en(0).format(p), '%');
      $('t-pool').querySelector('.t-sub').textContent = 'used';
      const m = $('t-pool').querySelector('.meter');
      m.setAttribute('aria-valuenow', p.toFixed(0));
      m.querySelector('span').style.width = `${Math.min(100, p).toFixed(1)}%`;
    }

    value('t-ctr', String(v.containers));
    const sub = $('t-ctr').querySelector('.t-sub');
    clear(sub).append('running');
    let foot = $('t-ctr').querySelector('.t-foot');
    if (!foot) { foot = h('p', { class: 't-foot' }); $('t-ctr').append(foot); }
    const healthy = v.disks && v.disks_ok === v.disks;
    clear(foot).append(h('span', { class: 'si', role: 'img', 'aria-label': healthy ? 'Healthy' : 'Attention' }, statusIcon(healthy ? 'good' : 'warn')),
      `${v.disks_ok}/${v.disks} disks healthy`);
  }

  function renderWeather(S) {
    const w = S.weather;
    if (!w) return;
    clear($('wx-icon')).append(wxIcon(w.code, w.day));
    setText('wx-temp', `${Math.round(w.temp)}°`);
    setText('wx-label', w.label);
    setText('wx-feels', `Feels like ${Math.round(w.feels)}° · ${w.max}° / ${w.min}°`);
    setText('wx-sub', `Updated ${time(new Date(w.at))} IST`);
    const rain = (w.rain || []).filter((r) => r[1] != null);
    const top = rain.length ? Math.max(...rain.map((r) => r[1])) : 0;
    let sum = 'No rain expected';
    if (top >= 20) {
      const hrs = rain.filter((r) => r[1] >= (top >= 50 ? 50 : 20));
      const from = hrs[0][0], to = hrs[hrs.length - 1][0];
      sum = `${top >= 50 ? 'Rain likely' : 'Some chance of rain'} ${from === to ? `around ${from}` : `${from}–${to}`} · up to ${top}%`;
    }
    setText('rain-sum', sum);
    rainBars($('rain-chart'), rain);
  }

  function renderFx(S) {
    const f = S.fx;
    if (!f || !f.series || !f.series.length) return;
    const ser = f.series, first = ser[0][1], last = ser[ser.length - 1][1], diff = last - first;
    setText('fx-rate', `₹${en(2).format(f.rate)}`);
    setText('fx-delta', `${diff >= 0 ? '▲' : '▼'} ₹${en(2).format(Math.abs(diff))} (${en(1).format((100 * Math.abs(diff)) / first)}%) since ${fDay.format(isoDay(ser[0][0]))}`);
    setText('fx-sub', `ECB reference rate · ${fDay.format(isoDay(f.date))}`);
    lineChart($('fx-chart'), [{ name: 'USD/INR', cls: 's1', pts: ser.map((x) => [isoDay(x[0]).getTime(), x[1]]) }], {
      fmtY: (v) => `₹${+v.toFixed(1)}`, fmtV: (v) => `₹${en(2).format(v)}`,
      fmtX: (x) => fDay.format(x), fmtTip: (x) => fLong.format(x), label: 'US dollar to Indian rupee, last 30 business days',
    });
    table('fx-table', ['Date', '₹ per $'], [...ser].reverse().map((x) => [fLong.format(isoDay(x[0])), en(2).format(x[1])]));
  }

  function renderMedia(S) {
    const m = S.media, st = S.streaming;
    if (m) {
      const items = [['Movies', m.movies], ['Shows', m.shows], ['Episodes', m.episodes], ['Songs', m.songs], ['Albums', m.albums], ['Artists', m.artists]];
      clear($('media-counts')).append(...items.map(([k, v]) => h('div', {}, h('dt', { text: k }), h('dd', { text: int(v) }))));
    }
    if (st) {
      const n = (st.video || 0) + (st.music || 0), e = $('streaming');
      e.dataset.on = n ? '1' : '0';
      e.querySelector('.txt').textContent = n ? `${n} playing now` : 'Nothing playing';
    }
  }

  function renderDns(S) {
    const p = S.pihole;
    if (!p || !p.days || !p.days.length) return;
    const y = p.days[p.days.length - 1];
    const share = y.queries ? (100 * y.blocked) / y.queries : 0;
    const blocked30 = p.days.reduce((a, d) => a + d.blocked, 0);
    const facts = [['Queries yesterday', int(y.queries)], ['Blocked yesterday', `${en(1).format(share)}%`],
      [`Blocked in ${p.days.length} days`, compact(blocked30)]];
    if (p.blocklist) facts.push(['Domains on blocklists', int(p.blocklist)]);
    clear($('dns-facts')).append(...facts.map(([k, val]) => h('div', {}, h('dt', { text: k }), h('dd', { text: val }))));
    columns($('dns-chart'), p.days);
    table('dns-table', ['Day', 'Queries', 'Blocked', '%'], [...p.days].reverse().map((d) => [
      fLong.format(isoDay(d.day)), int(d.queries), int(d.blocked), d.queries ? `${en(1).format((100 * d.blocked) / d.queries)}%` : '—']));
  }

  function renderFooter(S) {
    setText('generated', `Snapshot ${time(new Date(S.generated))} IST`);
    setText('year', String(new Date().getFullYear()));
  }

  // ---------------------------------------------------------------- load loop
  let S = null;
  function render() {
    for (const f of [renderOverall, renderApps, renderVitals, renderWeather, renderFx, renderMedia, renderDns, renderFooter]) {
      try { f(S); } catch (e) { console.error(f.name, e); }
    }
    sweep();
  }
  async function load() {
    try {
      const r = await fetch('/api/snapshot.json', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      S = await r.json();
      render();
    } catch (e) {
      if (!S) {
        const pill = $('overall');
        pill.dataset.state = 'none';
        clear(pill.querySelector('.si')).append(statusIcon('none'));
        pill.querySelector('.txt').textContent = 'Live status unavailable';
      }
    }
  }

  document.querySelectorAll('.app img').forEach((img) => {
    const swap = () => {
      const name = img.closest('.app').querySelector('.app-name').textContent;
      img.replaceWith(h('span', { class: 'mono-ico', 'aria-hidden': 'true', text: name.charAt(0) }));
    };
    if (img.complete && img.naturalWidth === 0) swap();
    else img.addEventListener('error', swap, { once: true });
  });

  // ---------------------------------------------------------------- "drop your email"
  // The form stays hidden without script (the page policy blocks plain form posts).
  // nginx just writes the request to a log; the header keeps other sites from posting.
  const THANKS = [
    'Email received. The admin has been notified and is now pretending to be busy.',
    'Noted. Your request joins a queue of one, run by a very sleepy admin.',
    'Delivered. The server hamsters are running it upstairs as we speak.',
    'Got it. If you hear nothing back, blame the Pi-hole. It blocks everything.',
  ];
  const ask = $('ask');
  if (ask) {
    ask.hidden = false;
    const input = $('ask-email'), button = ask.querySelector('button'), msg = $('ask-msg');
    const say = (text, state) => { msg.textContent = text; msg.dataset.state = state || ''; };
    ask.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = input.value.trim();
      if (!/^[^\s@]{1,64}@[^\s@]{1,190}\.[A-Za-z]{2,24}$/.test(email)) {
        say("That's not an email address. Nice try though.", 'err');
        input.focus();
        return;
      }
      button.disabled = true;
      say('Waking up the server…');
      try {
        const r = await fetch('/api/access', {
          method: 'POST', cache: 'no-store', body: `email=${encodeURIComponent(email)}`,
          headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'ask' },
        });
        if (r.status === 429) say('Easy there. The server has feelings. Try again in a minute.', 'err');
        else if (!r.ok) throw new Error(`HTTP ${r.status}`);
        else { input.value = ''; say(THANKS[Math.floor(Math.random() * THANKS.length)], 'ok'); }
      } catch (err) {
        say('Something tripped over a cable. Try again later.', 'err');
      } finally {
        button.disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------- activity
  // A few events for the owner's visitor log (nginx writes them; nothing typed is sent):
  // which app is opened, which sections are reached, leaving and returning to the tab,
  // theme and table toggles.
  function track(a, t) {
    try {
      fetch(`/api/e?a=${a}&t=${encodeURIComponent(t)}`, { keepalive: true, cache: 'no-store' }).catch(() => {});
    } catch (e) { /* never let this break the page */ }
  }
  document.querySelectorAll('a.app').forEach((a) => a.addEventListener('click', () => track('open', a.dataset.id)));
  document.querySelectorAll('#footer a[href^="https://github.com/"]').forEach((a) => a.addEventListener('click', () => track('open', 'source')));
  if ('IntersectionObserver' in window) {
    // "Reached" = half of the section, or 240px of a tall one, is on screen.
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.intersectionRect.height >= Math.min(240, e.boundingClientRect.height * 0.5)) {
          track('view', e.target.id);
          io.unobserve(e.target);
        }
      }
    }, { threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] });
    ['apps', 'vitals', 'around', 'dns', 'footer'].forEach((id) => { if ($(id)) io.observe($(id)); });
  }
  document.addEventListener('visibilitychange', () => track(document.hidden ? 'hide' : 'show', 'page'));
  document.querySelectorAll('.tbl-btn').forEach((b) => b.addEventListener('click', () => track('table', b.dataset.for || 'chart')));

  function tick() { setText('clock', `${time(Date.now())} IST`); }
  tick();
  setInterval(tick, 5000);

  function syncThemeButton() {
    const dark = root.getAttribute('data-mode') === 'dark';
    $('theme').setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  }
  $('theme').addEventListener('click', () => {
    const m = root.getAttribute('data-mode') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', m);
    root.setAttribute('data-mode', m);
    try { localStorage.setItem('theme', m); } catch (e) { /* not saved; still switches */ }
    track('theme', m);
    syncThemeButton();
  });
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      if (!root.hasAttribute('data-theme')) { root.setAttribute('data-mode', e.matches ? 'dark' : 'light'); syncThemeButton(); }
    });
  }
  syncThemeButton();

  load();
  setInterval(load, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
})();
