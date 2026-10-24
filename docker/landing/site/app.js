/* [[private:DOMAIN]] - the clock, the theme toggle, the footer's email box and the visitor log's
   pings and events. No libraries and no inline styles (the CSP forbids them), and every
   text value goes in via textContent, never innerHTML. */
'use strict';
(function () {
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
  const setText = (id, t) => { const e = $(id); if (e) e.textContent = t; };

  const fTime = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
  const time = (ms) => fTime.format(ms);

  document.querySelectorAll('.app img').forEach((img) => {
    const swap = () => {
      const name = img.closest('.app').querySelector('.app-name').textContent;
      img.replaceWith(h('span', { class: 'mono-ico', 'aria-hidden': 'true', text: name.charAt(0) }));
    };
    if (img.complete && img.naturalWidth === 0) swap();
    else img.addEventListener('error', swap, { once: true });
  });
  setText('year', String(new Date().getFullYear()));

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
  // and theme toggles.
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
    ['apps', 'media', 'footer'].forEach((id) => { if ($(id)) io.observe($(id)); });
  }
  document.addEventListener('visibilitychange', () => track(document.hidden ? 'hide' : 'show', 'page'));

  // Once a minute while the page is open, so the visitor log can tell how long it stayed open.
  function ping() {
    fetch('/api/ping', { cache: 'no-store' }).catch(() => {});
  }

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

  ping();
  setInterval(ping, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) ping(); });
})();
