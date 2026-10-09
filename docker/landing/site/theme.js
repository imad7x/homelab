/* Runs before first paint so the saved theme never flashes. */
(function () {
  var r = document.documentElement, t = null;
  try { t = localStorage.getItem('theme'); } catch (e) { /* private mode: follow the OS */ }
  if (t === 'light' || t === 'dark') r.setAttribute('data-theme', t);
  var dark = t ? t === 'dark' : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  r.setAttribute('data-mode', dark ? 'dark' : 'light');
})();
