// Kiosk behaviors apply only to the device's own display: the page must be on
// loopback AND the server must report Raspberry Pi hardware. A remote browser
// never inherits kiosk chrome. The kiosk latch (?kiosk=1 remembered in
// localStorage) survives the daily reload; opening the page from anywhere but
// loopback clears it so a phone on the LAN is never a kiosk.
(function () {
  const host = location.hostname;
  const isLoopback = host === 'localhost' || host === '127.0.0.1' ||
    host === '::1' || host === '[::1]';
  const isPi = document.body.dataset.isPi === 'true';

  const LATCH = 'glancecam.kiosk';
  const params = new URLSearchParams(location.search);
  if (params.get('kiosk') === '1') {
    try { localStorage.setItem(LATCH, '1'); } catch (e) { /* private mode */ }
  }
  if (!isLoopback) {
    // A non-loopback view is never a kiosk; drop any stale latch on this origin.
    try { localStorage.removeItem(LATCH); } catch (e) { /* ignore */ }
    return;
  }

  let latched = false;
  try { latched = localStorage.getItem(LATCH) === '1'; } catch (e) { /* ignore */ }
  const kiosk = (params.get('kiosk') === '1' || latched) && isPi;
  if (!kiosk) return;

  document.body.classList.add('kiosk');

  // Hide the cursor after a short idle, reveal it on any input.
  let idleTID = 0;
  const HIDE_AFTER = 5000;
  function activity() {
    document.body.classList.remove('cursor-hidden');
    clearTimeout(idleTID);
    idleTID = setTimeout(() => document.body.classList.add('cursor-hidden'), HIDE_AFTER);
  }
  ['mousemove', 'mousedown', 'touchstart', 'keydown'].forEach(
    (ev) => document.addEventListener(ev, activity, { passive: true }));
  activity();

  // Reload once at 04:00 local, daily, for memory hygiene on a long-lived kiosk.
  function scheduleNightlyReload() {
    const now = new Date();
    const next = new Date(now);
    next.setHours(4, 0, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    setTimeout(() => location.reload(), next - now);
  }
  scheduleNightlyReload();
})();
