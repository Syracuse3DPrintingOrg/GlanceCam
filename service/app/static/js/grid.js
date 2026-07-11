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

function applyLayout(entries) {
  const n = entries.length;
  if (!n) return;
  const aspects = entries.map(e => camAspect(e.cam));
  const avg = aspects.reduce((a, b) => a + b, 0) / aspects.length;
  const { cols, rows } = chooseLayout(n, window.innerWidth, window.innerHeight, avg);
  grid.style.setProperty('--gc-cols', cols);
  grid.style.setProperty('--gc-rows', rows);
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
    // Keep the sub stream on screen and warm the main stream behind it: the
    // camera is only dialed now and the picture cannot start before its next
    // keyframe, so a visible swap beats a black "loading" screen. If the main
    // stream never renders (unsupported codec, offline), the sub simply stays.
    startMainUpgrade(entry);
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

async function init() {
  if (!grid) return;
  let cams;
  try {
    const r = await fetch('/api/cameras');
    cams = r.ok ? await r.json() : null;
  } catch (e) { cams = null; }
  if (!Array.isArray(cams)) return;         // keep the server-rendered fallback

  cams = cams.filter(c => c.enabled !== false)
             .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  grid.innerHTML = '';
  if (cams.length === 0) {
    grid.innerHTML = '<div class="gc-empty"><p>No cameras yet.</p>' +
      '<a class="btn btn-primary" href="/settings">Add a camera</a></div>';
    return;
  }

  const entries = cams.map(buildTile);
  applyLayout(entries);

  const limit = await loadBudget(IS_KIOSK ? 'kiosk' : 'remote');
  entries.forEach((entry, i) => {
    if (i < limit) goLive(entry); else goPaused(entry);
  });

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

  // Keep our CSS state in step if the browser drops OS fullscreen (Esc).
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement && fullscreenEntry) exitFullscreen(entries);
  });

  let resizeTID = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTID);
    resizeTID = setTimeout(() => applyLayout(entries), 120);
  });
  window.addEventListener('orientationchange', () => applyLayout(entries));

  checkHealth();
}

init();
