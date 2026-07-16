// GlanceCam grid. Lays cameras edge to edge, plays the light sub stream on the
// tiles inside the decode budget, shows the rest as refreshed snapshots, and
// expands one tile to fill the screen on a click. Nothing here animates except
// the stream content.

const body = document.body;
const grid = document.getElementById('gc-grid');
const toastEl = document.getElementById('gc-toast');

const GO2RTC_PATH = body.dataset.go2rtcPath || '/go2rtc';
const SNAPSHOT_REFRESH = Math.max(2, parseInt(body.dataset.snapshotRefresh, 10) || 10) * 1000;
const FULLSCREEN_MAIN_DEFAULT = body.dataset.fullscreenMain === 'true';
const IS_KIOSK = body.classList.contains('kiosk');

// ---- Layout math (mirrored by a Python twin in tests/test_ui_layout) -------

// Choose the column/row split that gives the largest tile for N cameras in a
// W x H viewport, assuming a uniform tile aspect (w/h). Tiles letterbox with
// black inside, so the winner is the split whose fitted tile area is largest.
export function chooseLayout(n, w, h, aspect) {
  if (n <= 0) return { cols: 1, rows: 1 };
  if (!(aspect > 0)) aspect = 16 / 9;
  if (!(w > 0) || !(h > 0)) return { cols: Math.ceil(Math.sqrt(n)), rows: Math.ceil(n / Math.ceil(Math.sqrt(n))) };
  const target = w / h;
  let best = null;
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    const cellW = w / cols;
    const cellH = h / rows;
    // Fit the tile aspect inside the cell (contain).
    let tileW, tileH;
    if (cellW / cellH > aspect) { tileH = cellH; tileW = cellH * aspect; }
    else { tileW = cellW; tileH = cellW / aspect; }
    const area = tileW * tileH;
    const cand = { cols, rows, area };
    if (!best) { best = cand; continue; }
    if (area > best.area * 1.005) { best = cand; continue; }
    if (area >= best.area * 0.995) {
      // A real tie (e.g. 2x1 vs 1x2 for two 16:9 tiles): prefer the split whose
      // shape matches the viewport, then the one with fewer empty cells.
      const dCand = Math.abs(cols / rows - target);
      const dBest = Math.abs(best.cols / best.rows - target);
      if (dCand < dBest - 1e-9) { best = cand; continue; }
      if (Math.abs(dCand - dBest) < 1e-9 && cols * rows < best.cols * best.rows) best = cand;
    }
  }
  return { cols: best.cols, rows: best.rows };
}

function camAspect(cam) {
  const res = (cam.sub_url && cam.sub_resolution) ? cam.sub_resolution
    : (cam.main_resolution || cam.sub_resolution);
  if (Array.isArray(res) && res[0] > 0 && res[1] > 0) return res[0] / res[1];
  return 16 / 9;
}

// A camera this wide (Reolink Duo ~32:9, some panels 21:9) reads better as a
// double-width tile than squeezed into a single 16:9 cell.
const WIDE_ASPECT = 2.4;

// Automatic mixed-aspect packing (mirrored by a Python twin in
// tests/test_grid_pack.py). Wide cameras take a 2-column, 1-row cell; the rest
// are 1x1. Choose the cols/rows split that makes tiles largest (same area rule
// as chooseLayout, measured for a normal 16:9 tile in one cell), place the wide
// tiles first across the top rows, then fill the gaps with normal tiles.
export function packAuto(cameras, w, h) {
  const wide = cameras.filter(c => (c.aspect || 16 / 9) >= WIDE_ASPECT);
  const normal = cameras.filter(c => (c.aspect || 16 / 9) < WIDE_ASPECT);
  const demand = 2 * wide.length + normal.length;
  if (demand <= 0) return { cols: 1, rows: 1, cells: [] };
  if (!(w > 0) || !(h > 0)) { w = 16; h = 9; }
  const target = w / h;
  const aspect = 16 / 9;

  const place = (cols) => {
    const occ = [];
    const ensure = (r) => { while (occ.length <= r) occ.push(new Array(cols).fill(false)); };
    const cells = [];
    for (const cam of wide) {
      let r = 0;
      // eslint-disable-next-line no-constant-condition
      for (;;) {
        ensure(r);
        let placed = false;
        for (let c = 0; c + 1 < cols; c++) {
          if (!occ[r][c] && !occ[r][c + 1]) {
            occ[r][c] = occ[r][c + 1] = true;
            cells.push({ camera_id: cam.camera_id, col: c, row: r, w: 2, h: 1 });
            placed = true; break;
          }
        }
        if (placed) break;
        r++;
      }
    }
    for (const cam of normal) {
      let r = 0;
      for (;;) {
        ensure(r);
        let placed = false;
        for (let c = 0; c < cols; c++) {
          if (!occ[r][c]) {
            occ[r][c] = true;
            cells.push({ camera_id: cam.camera_id, col: c, row: r, w: 1, h: 1 });
            placed = true; break;
          }
        }
        if (placed) break;
        r++;
      }
    }
    return { cells, rows: occ.length };
  };

  const minCols = wide.length ? 2 : 1;
  let best = null;
  for (let cols = minCols; cols <= demand; cols++) {
    const { cells, rows } = place(cols);
    const cellW = w / cols;
    const cellH = h / rows;
    let tileW, tileH;
    if (cellW / cellH > aspect) { tileH = cellH; tileW = cellH * aspect; }
    else { tileW = cellW; tileH = cellW / aspect; }
    const area = tileW * tileH;
    const cand = { cols, rows, area, cells };
    if (!best || area > best.area * 1.005) { best = cand; continue; }
    if (area >= best.area * 0.995) {
      const dCand = Math.abs(cols / rows - target);
      const dBest = Math.abs(best.cols / best.rows - target);
      if (dCand < dBest - 1e-9) { best = cand; continue; }
      if (Math.abs(dCand - dBest) < 1e-9 && cols * rows < best.cols * best.rows) best = cand;
    }
  }
  return { cols: best.cols, rows: best.rows, cells: best.cells };
}

// Position one tile in the CSS grid from a placement cell (0-based col/row).
function placeTile(el, cell) {
  el.style.gridArea = `${cell.row + 1} / ${cell.col + 1} / span ${cell.h || 1} / span ${cell.w || 1}`;
}

// ---- Stream helpers --------------------------------------------------------

function streamSrc(name) {
  return `${GO2RTC_PATH}/api/ws?src=${encodeURIComponent(name)}`;
}
function gridStreamName(cam) {
  return cam.sub_url ? `${cam.id}_sub` : `${cam.id}_main`;
}
function mainStreamName(cam) {
  return `${cam.id}_main`;
}

function makeStreamEl(name) {
  const el = document.createElement('video-stream');
  el.setAttribute('mode', 'webrtc,mse,mjpeg');
  el.background = false;
  el.src = streamSrc(name);
  return el;
}

function detachStream(entry) {
  if (entry.streamEl) {
    entry.streamEl.remove();      // triggers the element's own disconnect/close
    entry.streamEl = null;
  }
}

function clearSnapshot(entry) {
  if (entry.refreshTimer) { clearInterval(entry.refreshTimer); entry.refreshTimer = null; }
  if (entry.imgEl) { entry.imgEl.remove(); entry.imgEl = null; }
}

// ---- Tile states -----------------------------------------------------------

function goLive(entry) {
  clearSnapshot(entry);
  entry.el.classList.remove('paused', 'offline');
  entry.badge.textContent = '';
  detachStream(entry);
  entry.streamEl = makeStreamEl(gridStreamName(entry.cam));
  entry.el.insertBefore(entry.streamEl, entry.el.firstChild);
  entry.mode = 'live';
  entry.lastInteract = Date.now();
}

function goPaused(entry) {
  detachStream(entry);
  entry.el.classList.add('paused');
  entry.badge.textContent = 'Paused. Tap for live.';
  const img = document.createElement('img');
  img.alt = entry.cam.name;
  img.decoding = 'async';
  const refresh = () => { img.src = `/cam/${entry.cam.id}/snapshot?t=${Date.now()}`; };
  img.addEventListener('error', () => {
    entry.el.classList.add('offline');
    entry.badge.textContent = 'No snapshot. Tap for live.';
  });
  img.addEventListener('load', () => { entry.el.classList.remove('offline'); });
  entry.imgEl = img;
  entry.el.insertBefore(img, entry.el.firstChild);
  refresh();
  entry.refreshTimer = setInterval(refresh, SNAPSHOT_REFRESH);
  entry.mode = 'paused';
}

// ---- Fullscreen ------------------------------------------------------------

let fullscreenEntry = null;

// H265/HEVC decode support in this browser, resolved once per page. Many
// desktop Chromium builds and most Raspberry Pi browsers cannot decode HEVC,
// so a fullscreen upgrade to an H265 main stream would render a black tile;
// we detect that up front and stay on the sub stream instead.
let hevcSupport = null;
function browserPlaysHevc() {
  if (hevcSupport !== null) return hevcSupport;
  let ok = false;
  try {
    const MS = window.MediaSource;
    if (MS && typeof MS.isTypeSupported === 'function') {
      ok = MS.isTypeSupported('video/mp4; codecs="hvc1.1.6.L153.B0"') ||
           MS.isTypeSupported('video/mp4; codecs="hev1.1.6.L153.B0"');
    }
  } catch (e) { ok = false; }
  hevcSupport = ok;
  return ok;
}

function isHevc(codec) {
  const c = (codec || '').toUpperCase();
  return c === 'H265' || c === 'HEVC';
}

// Warn at most once per camera that its main stream cannot play here.
const hevcWarned = new Set();
function warnHevcOnce(cam) {
  if (hevcWarned.has(cam.id)) return;
  hevcWarned.add(cam.id);
  toast(`${cam.name}: full quality is H265, which this browser cannot play; showing the sub stream.`);
}

function wantsMain(cam) {
  if (cam.fullscreen_uses_main === true) return true;
  if (cam.fullscreen_uses_main === false) return false;
  return FULLSCREEN_MAIN_DEFAULT;
}

function enterFullscreen(entry, entries) {
  fullscreenEntry = entry;
  entry.lastInteract = Date.now();
  // Other live tiles keep their sub streams: tearing them down made the whole
  // grid go dark on exit while every stream re-dialed and waited for a
  // keyframe. Subs are cheap (the budget already limits how many exist) and
  // an instant return to the grid matters more than idle decode behind the
  // fullscreen tile.
  entry.el.classList.add('fullscreen');
  if (wantsMain(entry.cam) && entry.cam.sub_url) {
    if (isHevc(entry.cam.main_codec) && !browserPlaysHevc()) {
      // The main stream is known-H265 and this browser cannot decode it, so a
      // main upgrade would just go black. Skip it entirely and stay on the sub
      // stream that is already playing; tell the viewer why, once.
      warnHevcOnce(entry.cam);
    } else {
      // Keep the sub stream on screen and warm the main stream behind it: the
      // camera is only dialed now and the picture cannot start before its next
      // keyframe, so a visible swap beats a black "loading" screen. If the main
      // stream never renders (unknown codec that turns out unsupported,
      // offline), the sub simply stays after the give-up timer.
      startMainUpgrade(entry);
    }
  } else if (wantsMain(entry.cam)) {
    detachStream(entry);
    entry.streamEl = makeStreamEl(mainStreamName(entry.cam));
    entry.el.insertBefore(entry.streamEl, entry.el.firstChild);
  }
  // On a real browser also take the OS fullscreen where allowed. Silent on
  // failure, and never on the kiosk (it is already full screen).
  if (!IS_KIOSK && document.documentElement.requestFullscreen) {
    entry.el.requestFullscreen().catch(() => {});
  }
}

function startMainUpgrade(entry) {
  cancelMainUpgrade(entry);
  const mainEl = makeStreamEl(mainStreamName(entry.cam));
  mainEl.classList.add('gc-main-upgrade');
  entry._mainEl = mainEl;
  entry.el.insertBefore(mainEl, entry.el.firstChild);
  const onPlaying = (ev) => {
    if (!ev.target || ev.target.tagName !== 'VIDEO') return;
    // The main stream is rendering: reveal it ON TOP of the sub. The sub
    // keeps playing underneath so leaving fullscreen is instant instead of
    // re-dialing the camera and waiting out another keyframe interval.
    mainEl.classList.remove('gc-main-upgrade');
    mainEl.classList.add('gc-main-active');
    mainEl.removeEventListener('playing', onPlaying, true);
    clearTimeout(entry._mainTimer);
    entry._mainTimer = null;
  };
  mainEl.addEventListener('playing', onPlaying, true);
  // Give up quietly if the main stream cannot start (H265 in a browser that
  // cannot decode it, camera offline): the sub stream stays on screen.
  entry._mainTimer = setTimeout(() => cancelMainUpgrade(entry), 15000);
}

function cancelMainUpgrade(entry) {
  if (entry._mainTimer) { clearTimeout(entry._mainTimer); entry._mainTimer = null; }
  if (entry._mainEl) { entry._mainEl.remove(); entry._mainEl = null; }
}

function exitFullscreen(entries) {
  const entry = fullscreenEntry;
  fullscreenEntry = null;
  if (!entry) return;
  entry.el.classList.remove('fullscreen');
  // Drop the main overlay; the sub stream underneath never stopped playing,
  // so the grid is showing video again immediately. Cameras without a sub
  // swapped their only element to main and swap back here.
  cancelMainUpgrade(entry);
  if (wantsMain(entry.cam) && !entry.cam.sub_url) {
    detachStream(entry);
    entry.streamEl = makeStreamEl(gridStreamName(entry.cam));
    entry.el.insertBefore(entry.streamEl, entry.el.firstChild);
  }
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

// ---- Live-slot swapping ----------------------------------------------------

function liveCount(entries) {
  return entries.filter(e => e.mode === 'live').length;
}

function swapToLive(entry, entries, limit) {
  if (liveCount(entries) >= limit) {
    // Evict the least recently interacted live tile.
    const live = entries.filter(e => e.mode === 'live');
    live.sort((a, b) => a.lastInteract - b.lastInteract);
    const evicted = live[0];
    if (evicted) {
      goPaused(evicted);
      goLive(entry);
      toast(`Now live: ${entry.cam.name}. Paused ${evicted.cam.name}.`);
      return;
    }
  }
  goLive(entry);
}

// ---- Toast -----------------------------------------------------------------

let toastTimer = null;
function toast(msg) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2000);
}

// ---- Build -----------------------------------------------------------------

function buildTile(cam) {
  const el = document.createElement('div');
  el.className = 'gc-tile';
  el.dataset.cameraId = cam.id;
  const label = document.createElement('div');
  label.className = 'gc-tile-label';
  label.textContent = cam.name;
  const badge = document.createElement('div');
  badge.className = 'gc-tile-badge';
  el.append(label, badge);
  grid.append(el);
  return { cam, el, badge, mode: 'paused', streamEl: null, imgEl: null,
           refreshTimer: null, lastInteract: 0, _suspended: false };
}

async function loadBudget(surface) {
  const q = new URLSearchParams({ surface });
  if (navigator.hardwareConcurrency) q.set('cores', navigator.hardwareConcurrency);
  q.set('width', window.innerWidth);
  q.set('height', window.innerHeight);
  try {
    const r = await fetch(`/api/system/budget?${q}`);
    if (r.ok) {
      const d = await r.json();
      if (Number.isFinite(d.live_tile_limit)) return d.live_tile_limit;
    }
  } catch (e) { /* degrade to a generous default below */ }
  return 9;
}

async function checkHealth() {
  try {
    const r = await fetch('/api/system');
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.go2rtc_healthy === false) {
      const b = document.createElement('a');
      b.className = 'gc-banner';
      b.href = '/settings#system';
      b.textContent = 'The stream engine is not reachable. Open settings.';
      body.append(b);
    }
  } catch (e) { /* a missing endpoint is not worth a banner */ }
}

// ---- Placement: turn cameras + the active layout into positioned cells ------

// Resolve the placements for the current render. In "auto" the packer sizes and
// positions every enabled camera; with a saved layout only its listed cells are
// shown (a layout is a deliberate subset), and a cell whose camera is gone or
// disabled is skipped. Returns { cols, rows, placements:[{cam, cell}] }.
function resolvePlacements(cams, layoutDoc) {
  const byId = new Map(cams.map(c => [c.id, c]));
  const active = layoutDoc && layoutDoc.active;
  const saved = active && active !== 'auto'
    ? (layoutDoc.layouts || []).find(l => l.id === active) : null;

  if (saved) {
    const placements = [];
    for (const cell of saved.cells || []) {
      const cam = byId.get(cell.camera_id);
      if (cam && cam.enabled !== false) placements.push({ cam, cell });
    }
    return { cols: saved.cols, rows: saved.rows, placements, mode: 'saved' };
  }

  const packed = packAuto(
    cams.map(c => ({ camera_id: c.id, aspect: camAspect(c) })),
    window.innerWidth, window.innerHeight);
  const placements = packed.cells
    .map(cell => ({ cam: byId.get(cell.camera_id), cell }))
    .filter(p => p.cam);
  return { cols: packed.cols, rows: packed.rows, placements, mode: 'auto' };
}

function applyGrid(cols, rows) {
  grid.style.setProperty('--gc-cols', cols);
  grid.style.setProperty('--gc-rows', rows);
}

// ---- Render (re-runnable so an edited layout re-renders) --------------------

let entries = [];
let limit = 9;
let layoutSig = null;   // detect a layout change on focus without a needless rebuild

function teardown() {
  if (fullscreenEntry) exitFullscreen(entries);
  for (const entry of entries) { detachStream(entry); clearSnapshot(entry); cancelMainUpgrade(entry); }
  entries = [];
  grid.innerHTML = '';
}

async function render(cams, layoutDoc) {
  teardown();

  if (cams.length === 0) {
    grid.innerHTML = '<div class="gc-empty"><p>No cameras yet.</p>' +
      '<a class="btn btn-primary" href="/settings">Add a camera</a></div>';
    return;
  }

  const { cols, rows, placements } = resolvePlacements(cams, layoutDoc);
  applyGrid(cols, rows);

  if (placements.length === 0) {
    grid.innerHTML = '<div class="gc-empty"><p>This layout has no cameras yet.</p>' +
      '<a class="btn btn-primary" href="/settings#grid">Edit the layout</a></div>';
    return;
  }

  entries = placements.map(({ cam, cell }) => {
    const entry = buildTile(cam);
    placeTile(entry.el, cell);
    entry.cell = cell;
    return entry;
  });

  limit = await loadBudget(IS_KIOSK ? 'kiosk' : 'remote');
  entries.forEach((entry, i) => {
    if (i < limit) goLive(entry); else goPaused(entry);
  });
}

// Auto mode depends on the viewport, so re-pack on resize. Reposition the
// existing tiles in place instead of rebuilding, so streams never re-dial.
function repackAuto(cams, layoutDoc) {
  if (!entries.length) return;
  if (layoutDoc && layoutDoc.active && layoutDoc.active !== 'auto') return;  // saved layout is fixed
  const { cols, rows, placements } = resolvePlacements(cams, layoutDoc);
  applyGrid(cols, rows);
  const byCam = new Map(placements.map(p => [p.cam.id, p.cell]));
  for (const entry of entries) {
    const cell = byCam.get(entry.cam.id);
    if (cell) { placeTile(entry.el, cell); entry.cell = cell; }
  }
}

// ---- Data + lifecycle -------------------------------------------------------

async function fetchCameras() {
  try {
    const r = await fetch('/api/cameras');
    const cams = r.ok ? await r.json() : null;
    if (!Array.isArray(cams)) return null;
    return cams.filter(c => c.enabled !== false).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  } catch (e) { return null; }
}

async function fetchLayouts() {
  try {
    const r = await fetch('/api/layouts');
    return r.ok ? await r.json() : { active: 'auto', layouts: [] };
  } catch (e) { return { active: 'auto', layouts: [] }; }
}

function signature(layoutDoc) {
  const active = (layoutDoc && layoutDoc.active) || 'auto';
  if (active === 'auto') return 'auto';
  const l = (layoutDoc.layouts || []).find(x => x.id === active);
  return JSON.stringify({ active, l });   // active id plus the active layout's shape
}

let lastCams = [];
let lastLayouts = { active: 'auto', layouts: [] };

async function refresh(force) {
  const [cams, layoutDoc] = await Promise.all([fetchCameras(), fetchLayouts()]);
  if (cams === null) return;   // keep the server-rendered fallback on a fetch miss
  const sig = signature(layoutDoc) + '|' + cams.map(c => c.id + (c.enabled === false ? 'x' : '')).join(',');
  if (!force && sig === layoutSig) return;
  layoutSig = sig;
  lastCams = cams;
  lastLayouts = layoutDoc;
  await render(cams, layoutDoc);
}

async function init() {
  if (!grid) return;

  grid.addEventListener('click', (e) => {
    const tileDiv = e.target.closest('.gc-tile');
    if (!tileDiv) return;
    const entry = entries.find(en => en.el === tileDiv);
    if (!entry) return;
    if (fullscreenEntry) { exitFullscreen(entries); return; }
    if (entry.mode === 'paused') { swapToLive(entry, entries, limit); return; }
    enterFullscreen(entry, entries);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && fullscreenEntry) exitFullscreen(entries);
  });
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement && fullscreenEntry) exitFullscreen(entries);
  });

  let resizeTID = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTID);
    resizeTID = setTimeout(() => repackAuto(lastCams, lastLayouts), 120);
  });
  window.addEventListener('orientationchange', () => repackAuto(lastCams, lastLayouts));

  // Re-render when the layout is edited in settings (another tab fires a
  // storage event), and re-check on focus in case an edit happened while away.
  window.addEventListener('storage', (e) => {
    if (e.key === 'gc-layout-changed') refresh(true);
  });
  window.addEventListener('focus', () => refresh(false));
  // The on-screen menu switches the active layout; re-render at once rather
  // than waiting for the next poll below.
  window.addEventListener('gc:layout-switched', () => refresh(true));
  // The storage event only reaches tabs in the SAME browser; the kiosk and
  // other devices never see it (and the kiosk never refocuses), so also poll.
  // refresh(false) compares a signature and rebuilds only on a real change,
  // so the steady-state cost is one small GET every few seconds.
  setInterval(() => refresh(false), 7000);

  await refresh(true);
  checkHealth();
}

init();
