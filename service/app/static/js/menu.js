// GlanceCam on-screen menu: a minimal translucent bar across the top for
// reading the time and switching layouts by touch alone. No layout editing
// here, only switching. It opens from the always-present top-left hot corner,
// a tap on the grid gap/background, or the "m" key, and auto-hides after a
// short idle or on any tap outside it. Built entirely in JS so index.html stays
// minimal and a grid rebuild never disturbs it (it lives on body, not the grid).
(function () {
  const body = document.body;
  const grid = document.getElementById('gc-grid');

  const HIDE_AFTER = 8000;
  let hideTID = 0;
  let open = false;

  // ---- DOM, built once ----------------------------------------------------

  // An invisible 48x48 zone in the top-left corner: the touch-only way in on
  // the kiosk, present on every surface.
  const hot = document.createElement('button');
  hot.type = 'button';
  hot.className = 'gc-menu-hot';
  hot.setAttribute('aria-label', 'Open menu');

  const bar = document.createElement('div');
  bar.className = 'gc-menu';
  bar.setAttribute('role', 'dialog');
  bar.setAttribute('aria-label', 'Menu');

  const clockWrap = document.createElement('div');
  clockWrap.className = 'gc-menu-clock';
  const timeEl = document.createElement('div');
  timeEl.className = 'gc-menu-time';
  const dateEl = document.createElement('div');
  dateEl.className = 'gc-menu-date';
  clockWrap.append(timeEl, dateEl);

  const pills = document.createElement('div');
  pills.className = 'gc-menu-layouts';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'gc-menu-close';
  closeBtn.setAttribute('aria-label', 'Close menu');
  closeBtn.textContent = '×';

  bar.append(clockWrap, pills, closeBtn);
  body.append(hot, bar);

  // ---- Clock: update text only, never rebuild the bar ---------------------

  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                'Friday', 'Saturday'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December'];
  function two(n) { return n < 10 ? '0' + n : '' + n; }
  function tick() {
    const now = new Date();
    timeEl.textContent = two(now.getHours()) + ':' + two(now.getMinutes());
    dateEl.textContent = DAYS[now.getDay()] + ', ' + now.getDate() + ' ' +
      MONTHS[now.getMonth()];
  }
  tick();
  setInterval(tick, 1000);

  // ---- Layout switcher pills ----------------------------------------------

  async function loadLayouts() {
    let doc = { active: 'auto', layouts: [] };
    try {
      const r = await fetch('/api/layouts');
      if (r.ok) doc = await r.json();
    } catch (e) { /* offline: still offer Automatic */ }
    renderPills(doc);
  }

  function renderPills(doc) {
    const active = (doc && doc.active) || 'auto';
    const items = [{ id: 'auto', name: 'Automatic' }].concat(
      ((doc && doc.layouts) || []).map((l) => ({ id: l.id, name: l.name || 'Layout' })));
    pills.textContent = '';
    for (const it of items) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'gc-menu-pill' + (it.id === active ? ' gc-menu-pill-on' : '');
      b.textContent = it.name;
      b.addEventListener('click', () => switchLayout(it.id));
      pills.append(b);
    }
  }

  async function switchLayout(id) {
    try {
      await fetch('/api/layouts/active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      // Ask the grid to re-render now; its poll would catch up regardless.
      window.dispatchEvent(new CustomEvent('gc:layout-switched'));
    } catch (e) { /* the grid poll still picks the change up */ }
    close();
  }

  // ---- Open / close -------------------------------------------------------

  function resetHide() {
    clearTimeout(hideTID);
    hideTID = setTimeout(close, HIDE_AFTER);
  }
  function openMenu() {
    if (open) return;
    open = true;
    body.classList.add('gc-menu-open');
    loadLayouts();
    resetHide();
  }
  function close() {
    if (!open) return;
    open = false;
    body.classList.remove('gc-menu-open');
    clearTimeout(hideTID);
  }
  function toggle() { if (open) close(); else openMenu(); }

  // ---- Triggers -----------------------------------------------------------

  hot.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });

  if (grid) {
    // A tap on the grid container itself is a tap on the gap/background (a tile
    // tap stops here inside grid.js). stopPropagation so the outside-close
    // handler below does not immediately shut the menu we just opened.
    grid.addEventListener('click', (e) => {
      if (e.target === grid) { e.stopPropagation(); openMenu(); }
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open) { close(); return; }
    if (e.key === 'm' || e.key === 'M') {
      const t = e.target;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      toggle();
    }
  });

  // Any tap outside the bar (and not on the hot corner) closes it.
  document.addEventListener('click', (e) => {
    if (!open) return;
    if (bar.contains(e.target) || hot.contains(e.target)) return;
    close();
  });

  // Any interaction inside the bar keeps it alive past the idle timer.
  bar.addEventListener('pointerdown', resetHide);
  bar.addEventListener('click', resetHide);
  closeBtn.addEventListener('click', close);
})();
