// GlanceCam settings wiring: the camera list (badges, inline edit, delete,
// drag to reorder), the add-by-hand form, the display/access saves, and the
// system readout with the live-tile budget. Discovery lives in discovery.js.

const FULLSCREEN_MAIN_DEFAULT = document.body.dataset.fullscreenMain === 'true';

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* empty body is fine */ }
  return { ok: resp.ok, status: resp.status, data };
}

function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) {
    if (v !== '') out[k] = v;
  }
  return out;
}

// ---- Saved credentials -----------------------------------------------------

// One cache shared by the saved-credentials card and every picker on the page
// (the add form, the discovery credentials, and each camera edit form).
let credsCache = [];

function credOptionsHtml(selectedId) {
  return credsCache.map((c) => {
    const label = c.username ? `${c.name} (${c.username})` : c.name;
    return `<option value="${escapeAttr(c.id)}" ${c.id === selectedId ? 'selected' : ''}>${escapeHtml(label)}</option>`;
  }).join('');
}

// Refill every saved-credential <select>, keeping its first ("enter manually")
// option and current choice, then sync its manual username/password fields.
function populateCredPickers() {
  document.querySelectorAll('select.gc-cred-picker').forEach((sel) => {
    const keep = sel.value;
    const first = sel.querySelector('option');
    sel.innerHTML = (first ? first.outerHTML : '') + credOptionsHtml(keep);
    if (![...sel.options].some((o) => o.value === keep)) sel.value = '';
    applyCredPicker(sel);
  });
}

// A chosen saved set disables the manual username/password in the same scope, so
// it is clear which login will be used and the two cannot fight.
function applyCredPicker(sel) {
  const scope = sel.closest('[data-cred-scope]');
  if (!scope) return;
  const chosen = !!sel.value;
  ['.gc-cred-user', '.gc-cred-pass'].forEach((q) => {
    const el = scope.querySelector(q);
    if (!el) return;
    el.disabled = chosen;
    if (chosen) el.value = '';
  });
}

document.addEventListener('change', (e) => {
  if (e.target && e.target.matches && e.target.matches('select.gc-cred-picker')) {
    applyCredPicker(e.target);
  }
});

async function loadCredentials() {
  const { ok, data } = await api('GET', '/api/credentials');
  credsCache = ok && Array.isArray(data) ? data : [];
  renderCredList();
  populateCredPickers();
  document.dispatchEvent(new CustomEvent('gc-creds-changed'));
}

function renderCredList() {
  const list = document.getElementById('gc-cred-list');
  if (!list) return;
  list.innerHTML = '';
  if (!credsCache.length) {
    list.innerHTML = '<li class="list-group-item text-secondary">No saved logins yet.</li>';
    return;
  }
  for (const c of credsCache) {
    const li = document.createElement('li');
    li.className = 'list-group-item d-flex align-items-center';
    const who = c.username ? ` <span class="text-secondary small">${escapeHtml(c.username)}</span>` : '';
    li.innerHTML = `<span class="flex-grow-1"><strong>${escapeHtml(c.name)}</strong>${who}</span>`;
    const del = btn('Remove', 'btn-outline-danger');
    del.addEventListener('click', async () => {
      if (!confirm(`Remove saved login "${c.name}"?`)) return;
      await api('DELETE', `/api/credentials/${c.id}`);
      loadCredentials();
    });
    li.append(del);
    list.append(li);
  }
}

const credForm = document.getElementById('gc-cred-form');
if (credForm) {
  credForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const note = document.getElementById('gc-cred-note');
    const { ok, data } = await api('POST', '/api/credentials', formData(credForm));
    if (note) note.textContent = ok ? 'Saved' : ((data && data.detail) || 'Could not save');
    if (ok) { credForm.reset(); loadCredentials(); }
  });
}

// ---- Camera list -----------------------------------------------------------

function badges(cam) {
  const out = [];
  if (cam.enabled === false) out.push(['secondary', 'disabled']);
  if (cam.main_url && cam.sub_url) out.push(['success', 'live']);
  else if (cam.main_url && !cam.sub_url) out.push(['warning', 'no sub stream']);
  else if (!cam.main_url && cam.snapshot_url) out.push(['info', 'snapshot only']);
  if (cam.source && cam.source !== 'manual') out.push(['dark', cam.source]);
  return out.map(([c, t]) =>
    `<span class="badge bg-${c} gc-badge-dot">${t}</span>`).join(' ');
}

let cameraCache = [];

async function loadCameras() {
  const { ok, data } = await api('GET', '/api/cameras');
  const list = document.getElementById('gc-camera-list');
  if (!list) return;
  cameraCache = ok && Array.isArray(data) ? data : [];
  list.innerHTML = '';
  if (cameraCache.length === 0) {
    list.innerHTML = '<li class="list-group-item text-secondary">No cameras yet.</li>';
    return;
  }
  cameraCache
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
    .forEach((cam) => list.append(cameraRow(cam)));
  // The edit forms carry saved-credential pickers; fill any just rendered.
  populateCredPickers();
}

function cameraRow(cam) {
  const li = document.createElement('li');
  li.className = 'list-group-item';
  li.dataset.id = cam.id;
  li.draggable = true;

  const row = document.createElement('div');
  row.className = 'd-flex align-items-center gc-cam-row';
  row.innerHTML =
    `<span class="gc-cam-handle" title="Drag to reorder">&#8942;&#8942;</span>` +
    `<span class="flex-grow-1"><strong>${escapeHtml(cam.name)}</strong> ${badges(cam)}</span>`;

  const edit = btn('Edit', 'btn-outline-secondary');
  const del = btn('Remove', 'btn-outline-danger');
  row.append(edit, del);
  li.append(row);

  const editor = editForm(cam, li);
  editor.classList.add('d-none');
  li.append(editor);

  edit.addEventListener('click', () => editor.classList.toggle('d-none'));
  del.addEventListener('click', async () => {
    if (!confirm(`Remove ${cam.name}?`)) return;
    await api('DELETE', `/api/cameras/${cam.id}`);
    loadCameras();
  });

  wireDrag(li);
  return li;
}

function editForm(cam, li) {
  const wrap = document.createElement('div');
  wrap.className = 'gc-inline-edit border rounded p-3 mt-2';
  wrap.setAttribute('data-cred-scope', '');
  const fsMain = cam.fullscreen_uses_main === true ? 'on'
    : cam.fullscreen_uses_main === false ? 'off' : 'default';
  wrap.innerHTML = `
    <div class="mb-2"><label class="form-label small">Name</label>
      <input class="form-control form-control-sm" name="name" value="${escapeAttr(cam.name)}"></div>
    <div class="mb-2"><label class="form-label small">Main stream</label>
      <input class="form-control form-control-sm" name="main_url" value="${escapeAttr(cam.main_url || '')}"></div>
    <div class="mb-2"><label class="form-label small">Sub stream</label>
      <input class="form-control form-control-sm" name="sub_url" value="${escapeAttr(cam.sub_url || '')}"></div>
    <div class="mb-2"><label class="form-label small">Snapshot</label>
      <input class="form-control form-control-sm" name="snapshot_url" value="${escapeAttr(cam.snapshot_url || '')}"></div>
    <div class="mb-2"><label class="form-label small">Saved credentials</label>
      <select class="form-select form-select-sm gc-cred-picker" name="credential_id">
        <option value="">Enter a login below</option>
      </select></div>
    <div class="row g-2 mb-2">
      <div class="col"><label class="form-label small">Username</label>
        <input class="form-control form-control-sm gc-cred-user" name="username" value="${escapeAttr(cam.username || '')}" autocomplete="off"></div>
      <div class="col"><label class="form-label small">Password</label>
        <input class="form-control form-control-sm gc-cred-pass" type="password" name="password"
               placeholder="${cam.password === '__set__' ? 'Leave blank to keep' : ''}" autocomplete="new-password"></div>
    </div>
    <div class="row g-2 mb-2 align-items-end">
      <div class="col"><label class="form-label small">Full screen stream</label>
        <select class="form-select form-select-sm" name="fs">
          <option value="default" ${fsMain === 'default' ? 'selected' : ''}>Use the display default</option>
          <option value="on" ${fsMain === 'on' ? 'selected' : ''}>Main stream</option>
          <option value="off" ${fsMain === 'off' ? 'selected' : ''}>Keep the sub stream</option>
        </select></div>
      <div class="col form-check ms-2 mb-1">
        <input class="form-check-input" type="checkbox" name="enabled" id="en-${cam.id}" ${cam.enabled !== false ? 'checked' : ''}>
        <label class="form-check-label small" for="en-${cam.id}">Shown on the grid</label></div>
    </div>
    <button class="btn btn-primary btn-sm" type="button">Save</button>
    <span class="small ms-2"></span>`;

  const save = wrap.querySelector('button');
  const note = wrap.querySelector('span.small');
  save.addEventListener('click', async () => {
    const payload = {};
    wrap.querySelectorAll('input[name], select[name]').forEach((el) => {
      if (el.type === 'checkbox') { payload[el.name] = el.checked; return; }
      if (el.name === 'password' && el.value === '') return;  // keep the stored one
      payload[el.name] = el.value;
    });
    const fs = payload.fs; delete payload.fs;
    payload.fullscreen_uses_main = fs === 'on' ? true : fs === 'off' ? false : FULLSCREEN_MAIN_DEFAULT;
    const { ok, data } = await api('PATCH', `/api/cameras/${cam.id}`, payload);
    note.textContent = ok ? 'Saved' : ((data && data.detail) || 'Save failed');
    if (ok) setTimeout(loadCameras, 500);
  });
  return wrap;
}

// ---- Drag to reorder -------------------------------------------------------

let dragEl = null;
function wireDrag(li) {
  li.addEventListener('dragstart', () => { dragEl = li; li.classList.add('dragging'); });
  li.addEventListener('dragend', async () => {
    li.classList.remove('dragging');
    dragEl = null;
    const ids = Array.from(document.querySelectorAll('#gc-camera-list > li'))
      .map((el) => el.dataset.id).filter(Boolean);
    await api('POST', '/api/cameras/reorder', { ids });
  });
  li.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (!dragEl || dragEl === li) return;
    const list = li.parentNode;
    const rect = li.getBoundingClientRect();
    const after = e.clientY > rect.top + rect.height / 2;
    list.insertBefore(dragEl, after ? li.nextSibling : li);
  });
}

// ---- Add by hand + test ----------------------------------------------------

const addForm = document.getElementById('gc-add-camera');
if (addForm) {
  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const { ok, data } = await api('POST', '/api/cameras', formData(addForm));
    if (ok) { addForm.reset(); loadCameras(); }
    else alert((data && data.detail) || 'Could not add that camera.');
  });
  document.getElementById('gc-test-camera').addEventListener('click', async () => {
    const result = document.getElementById('gc-test-result');
    result.textContent = 'Testing...';
    const { data } = await api('POST', '/api/cameras/test', formData(addForm));
    if (data && data.ok) {
      const res = data.resolution ? ` (${data.resolution[0]}x${data.resolution[1]})` : '';
      result.textContent = 'Reachable' + res;
    } else {
      result.textContent = (data && data.error) || 'Could not reach that camera.';
    }
  });
}

// ---- Display / access saves ------------------------------------------------

for (const id of ['gc-display-form', 'gc-access-form']) {
  const form = document.getElementById(id);
  if (!form) continue;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = formData(form);
    form.querySelectorAll('input[type=checkbox]').forEach((cb) => { payload[cb.name] = cb.checked; });
    const { ok } = await api('POST', '/api/settings', payload);
    flash(form.querySelector('button[type=submit]'), ok);
  });
}

const clearBtn = document.getElementById('gc-password-clear');
if (clearBtn) {
  clearBtn.addEventListener('click', async () => {
    if (!confirm('Remove the settings password? Anyone on your network will be able to change settings.')) return;
    const { ok } = await api('POST', '/api/settings', { settings_password_clear: true });
    flash(clearBtn, ok);
    if (ok) setTimeout(() => location.reload(), 800);
  });
}

function flash(btn, ok) {
  if (!btn) return;
  const original = btn.textContent;
  btn.textContent = ok ? 'Saved' : 'Save failed';
  setTimeout(() => { btn.textContent = original; }, 1500);
}

// ---- System + budget -------------------------------------------------------

async function loadSystem() {
  const box = document.getElementById('gc-system');
  if (!box) return;
  const { ok, data } = await api('GET', '/api/system');
  if (!ok || !data) { box.innerHTML = '<div>System details are unavailable.</div>'; return; }

  const q = new URLSearchParams({ surface: 'remote' });
  if (navigator.hardwareConcurrency) q.set('cores', navigator.hardwareConcurrency);
  q.set('width', window.innerWidth);
  q.set('height', window.innerHeight);
  let budget = null;
  try {
    const r = await fetch(`/api/system/budget?${q}`);
    if (r.ok) budget = await r.json();
  } catch (e) { /* budget is optional */ }

  const hw = data.hardware_class ? ` (${data.hardware_class})` : '';
  let html =
    `<div>Version ${data.version}</div>` +
    `<div>Stream engine: ${data.go2rtc_healthy ? 'reachable' : 'not reachable'}</div>` +
    `<div>Cameras: ${data.camera_count}</div>` +
    `<div>Raspberry Pi: ${data.is_pi ? 'yes' : 'no'}${hw}</div>`;
  if (budget && Number.isFinite(budget.live_tile_limit)) {
    html += `<div class="mt-2">${data.camera_count} cameras, ` +
      `${budget.live_tile_limit} live at once on this screen.</div>`;
    if (Array.isArray(budget.recommendations) && budget.recommendations.length) {
      html += '<ul class="mb-0 mt-1">' +
        budget.recommendations.map((r) => `<li>${escapeHtml(r)}</li>`).join('') + '</ul>';
    }
  }
  box.innerHTML = html;
}

// ---- Small utilities -------------------------------------------------------

function btn(text, cls) {
  const b = document.createElement('button');
  b.className = `btn btn-sm ${cls} ms-2`;
  b.type = 'button';
  b.textContent = text;
  return b;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function escapeAttr(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// Exposed for discovery.js to refresh the list after a one-tap add, refresh the
// credential pickers after it saves a login, and know whether any login exists.
window.gcReloadCameras = loadCameras;
window.gcReloadCredentials = loadCredentials;
window.gcCredentialCount = () => credsCache.length;
window.gcEscapeHtml = escapeHtml;
window.gcApi = api;

loadCameras();
loadCredentials();
loadSystem();

// ---- Grid builder ----------------------------------------------------------
// Named layouts are a first-class resource (GET/POST/DELETE /api/layouts). This
// section picks the active layout and edits saved ones with a touch-friendly
// preview. Wrapped in an IIFE so its helpers never leak into discovery.js.
(() => {
  'use strict';
  const MAX_SPAN = 6;
  const WIDE_ASPECT = 2.4;

  const activeSel = document.getElementById('gc-layout-active');
  const builder = document.getElementById('gc-builder');
  if (!activeSel || !builder) return;

  const preview = document.getElementById('gc-builder-preview');
  const chipsBox = document.getElementById('gc-builder-chips');
  const nameInput = document.getElementById('gc-builder-name');
  const msgEl = document.getElementById('gc-builder-msg');
  const colsVal = document.getElementById('gc-cols-val');
  const rowsVal = document.getElementById('gc-rows-val');
  const saveBtn = document.getElementById('gc-builder-save');

  let layoutsCache = [];
  let activeId = 'auto';
  // The layout being built. cells: [{camera_id, col, row, w, h}].
  const state = { editingId: null, cols: 3, rows: 2, cells: [], chip: null };

  function notifyGridTabs() {
    // Fires a storage event in the grid tab (kiosk or another browser) so it
    // re-renders. The setting tab does not receive its own storage event.
    try { localStorage.setItem('gc-layout-changed', String(Date.now())); } catch (e) { /* private mode */ }
  }

  function camById(id) { return (cameraCache || []).find((c) => c.id === id); }

  function camAspect(cam) {
    const res = (cam && cam.sub_url && cam.sub_resolution) ? cam.sub_resolution
      : (cam && (cam.main_resolution || cam.sub_resolution));
    if (Array.isArray(res) && res[0] > 0 && res[1] > 0) return res[0] / res[1];
    return 16 / 9;
  }

  // ---- Active layout picker ------------------------------------------------

  async function loadLayouts() {
    const { ok, data } = await api('GET', '/api/layouts');
    layoutsCache = ok && data && Array.isArray(data.layouts) ? data.layouts : [];
    activeId = (ok && data && data.active) || 'auto';
    renderPicker();
  }

  function renderPicker() {
    const opts = ['<option value="auto">Automatic</option>'];
    for (const l of layoutsCache) {
      opts.push(`<option value="${escapeAttr(l.id)}">${escapeHtml(l.name)}</option>`);
    }
    activeSel.innerHTML = opts.join('');
    activeSel.value = layoutsCache.some((l) => l.id === activeId) ? activeId : 'auto';
  }

  document.getElementById('gc-layout-activate').addEventListener('click', async () => {
    const { ok } = await api('POST', '/api/layouts/active', { id: activeSel.value });
    if (ok) { activeId = activeSel.value; notifyGridTabs(); flash(document.getElementById('gc-layout-activate'), true); }
  });

  document.getElementById('gc-layout-delete').addEventListener('click', async () => {
    const id = activeSel.value;
    if (id === 'auto') { alert('Automatic cannot be deleted.'); return; }
    const l = layoutsCache.find((x) => x.id === id);
    if (!confirm(`Delete layout "${l ? l.name : id}"?`)) return;
    const { ok } = await api('DELETE', `/api/layouts/${id}`);
    if (ok) { closeBuilder(); await loadLayouts(); notifyGridTabs(); }
  });

  document.getElementById('gc-layout-new').addEventListener('click', () => {
    state.editingId = null;
    state.cols = 3; state.rows = 2; state.cells = []; state.chip = null;
    nameInput.value = '';
    openBuilder();
  });

  document.getElementById('gc-layout-edit').addEventListener('click', () => {
    const id = activeSel.value;
    const l = layoutsCache.find((x) => x.id === id);
    if (!l) { alert('Pick a saved layout to edit, or choose New layout.'); return; }
    state.editingId = l.id;
    state.cols = l.cols; state.rows = l.rows;
    state.cells = (l.cells || []).map((c) => ({ ...c }));
    state.chip = null;
    nameInput.value = l.name || '';
    openBuilder();
  });

  document.getElementById('gc-builder-cancel').addEventListener('click', closeBuilder);

  function openBuilder() { builder.classList.remove('d-none'); renderBuilder(); }
  function closeBuilder() { builder.classList.add('d-none'); state.chip = null; }

  // ---- Steppers ------------------------------------------------------------

  builder.querySelectorAll('.gc-stepper').forEach((box) => {
    const which = box.dataset.step;   // "cols" or "rows"
    box.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', () => {
        const next = Math.min(MAX_SPAN, Math.max(1, state[which] + parseInt(b.dataset.d, 10)));
        if (next === state[which]) return;
        state[which] = next;
        // Drop any tile that no longer fits the smaller grid.
        state.cells = state.cells.filter((c) => c.col + c.w <= state.cols && c.row + c.h <= state.rows);
        renderBuilder();
      });
    });
  });

  // ---- Occupancy + placement rules (mirror of the backend validator) -------

  function occupancy(exclude) {
    const occ = new Set();
    for (const c of state.cells) {
      if (c === exclude) continue;
      for (let cc = c.col; cc < c.col + c.w; cc++) {
        for (let rr = c.row; rr < c.row + c.h; rr++) occ.add(`${cc},${rr}`);
      }
    }
    return occ;
  }

  // A w x h block at (col,row) is placeable when it stays in bounds and no cell
  // it would cover is already taken. Mirrors validate_layout in layouts.py.
  function fits(col, row, w, h, occ) {
    if (col < 0 || row < 0 || col + w > state.cols || row + h > state.rows) return false;
    for (let cc = col; cc < col + w; cc++) {
      for (let rr = row; rr < row + h; rr++) if (occ.has(`${cc},${rr}`)) return false;
    }
    return true;
  }

  function placeAt(camId, col, row, w) {
    if (state.cells.some((c) => c.camera_id === camId)) return false;  // already placed
    if (!fits(col, row, w, 1, occupancy())) return false;
    state.cells.push({ camera_id: camId, col, row, w, h: 1 });
    return true;
  }

  // Drop at the first free slot: the accessible fallback for a plain tap on a
  // tray chip (no drag). Ultrawide cameras still prefer a double-width slot.
  function placeFirstFree(camId) {
    const cam = camById(camId);
    const occ = occupancy();
    for (let r = 0; r < state.rows; r++) {
      for (let c = 0; c < state.cols; c++) {
        let w = 1;
        if (cam && camAspect(cam) >= WIDE_ASPECT && fits(c, r, 2, 1, occ)) w = 2;
        if (fits(c, r, w, 1, occ)) {
          state.cells.push({ camera_id: camId, col: c, row: r, w, h: 1 });
          renderBuilder();
          return;
        }
      }
    }
  }

  function removeCell(cell) {
    state.cells = state.cells.filter((c) => c !== cell);
    renderBuilder();
  }

  // ---- Pointer-driven direct manipulation ----------------------------------
  // One engine for three gestures, all on Pointer Events so a mouse and the Pi
  // touchscreen share a code path (HTML5 dragstart has no touch support):
  //   place  - drag a tray chip onto the grid
  //   move   - drag a placed tile (or its dimmed tray chip) to a new spot
  //   resize - drag a tile's corner handle to change its span
  // A small move threshold separates a drag from a tap; a tap on a tile just
  // toggles its remove control.
  const MOVE_THRESHOLD = 6;   // px before a press counts as a drag, not a tap
  let drag = null;
  let ghostEl = null;
  let dropEl = null;

  // Map a client point to a 0-based cell, or null when outside the preview.
  // Gaps (4px) are folded into the even split; close enough for snapping.
  function cellFromPoint(x, y, clamp) {
    const rect = preview.getBoundingClientRect();
    const pad = 4;
    const iw = rect.width - pad * 2;
    const ih = rect.height - pad * 2;
    if (iw <= 0 || ih <= 0) return null;
    const fx = (x - rect.left - pad) / iw;
    const fy = (y - rect.top - pad) / ih;
    if (!clamp && (fx < 0 || fx > 1 || fy < 0 || fy > 1)) return null;
    let col = Math.floor(fx * state.cols);
    let row = Math.floor(fy * state.rows);
    col = Math.min(state.cols - 1, Math.max(0, col));
    row = Math.min(state.rows - 1, Math.max(0, row));
    return { col, row };
  }

  function showDrop(col, row, w, h, valid) {
    if (!dropEl) {
      dropEl = document.createElement('div');
      dropEl.className = 'gc-b-drop';
      preview.append(dropEl);
    }
    dropEl.style.gridArea = `${row + 1} / ${col + 1} / span ${h} / span ${w}`;
    dropEl.classList.toggle('gc-b-drop-bad', !valid);
    dropEl.classList.toggle('gc-b-drop-ok', valid);
  }
  function clearDrop() { if (dropEl) { dropEl.remove(); dropEl = null; } }

  function makeGhost(label) {
    ghostEl = document.createElement('div');
    ghostEl.className = 'gc-b-ghost';
    ghostEl.textContent = label;
    document.body.append(ghostEl);
  }
  function positionGhost(x, y) {
    if (ghostEl) { ghostEl.style.left = `${x}px`; ghostEl.style.top = `${y}px`; }
  }

  function flashInvalid() {
    preview.classList.remove('gc-b-shake');
    void preview.offsetWidth;   // reflow so the animation restarts every time
    preview.classList.add('gc-b-shake');
    setTimeout(() => preview.classList.remove('gc-b-shake'), 300);
  }

  function beginDrag(e, d) {
    if (drag) return;   // ignore a second finger mid-drag
    if (e.button != null && e.button > 0) return;   // primary button / touch only
    drag = d;
    drag.pointerId = e.pointerId;
    drag.startX = e.clientX;
    drag.startY = e.clientY;
    drag.moved = false;
    drag.dropTarget = null;
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerCancel);
    e.preventDefault();
  }

  function cleanupDrag() {
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
    window.removeEventListener('pointercancel', onPointerCancel);
    if (ghostEl) { ghostEl.remove(); ghostEl = null; }
    preview.classList.remove('gc-b-live');
    drag = null;
  }

  function onPointerMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.moved) {
      if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < MOVE_THRESHOLD) return;
      drag.moved = true;
      preview.classList.add('gc-b-live');
      if (drag.kind !== 'resize') makeGhost(drag.label);
      if (drag.tileEl) drag.tileEl.classList.add('gc-b-lift');
    }
    e.preventDefault();
    if (drag.kind === 'resize') updateResize(e);
    else updatePlaceMove(e);
  }

  function updatePlaceMove(e) {
    positionGhost(e.clientX, e.clientY);
    const hit = cellFromPoint(e.clientX, e.clientY, false);
    if (!hit) {
      clearDrop();
      drag.dropTarget = null;
      if (ghostEl) ghostEl.classList.add('gc-b-ghost-out');
      return;
    }
    if (ghostEl) ghostEl.classList.remove('gc-b-ghost-out');

    let w, h, occ, cam = null;
    if (drag.kind === 'move') {
      w = drag.cell.w; h = drag.cell.h;
      occ = occupancy(drag.cell);   // the tile's own cells are free while it moves
    } else {
      w = 1; h = 1;
      occ = occupancy();
      cam = camById(drag.camId);
    }
    // Keep the span in bounds by pulling the top-left corner back from the edge.
    let col = Math.max(0, Math.min(hit.col, state.cols - w));
    let row = Math.max(0, Math.min(hit.row, state.rows - h));
    // Ultrawide cameras land 2x1 when a double slot is free at the hovered spot.
    if (drag.kind === 'place' && cam && camAspect(cam) >= WIDE_ASPECT && state.cols >= 2) {
      const c2 = Math.max(0, Math.min(hit.col, state.cols - 2));
      if (fits(c2, row, 2, 1, occ)) { w = 2; col = c2; }
    }
    const valid = fits(col, row, w, h, occ);
    drag.dropTarget = { col, row, w, h, valid };
    showDrop(col, row, w, h, valid);
  }

  function updateResize(e) {
    const cell = drag.cell;
    const t = cellFromPoint(e.clientX, e.clientY, true);
    if (!t) return;
    let w = Math.max(1, t.col - cell.col + 1);
    let h = Math.max(1, t.row - cell.row + 1);
    w = Math.min(w, state.cols - cell.col);
    h = Math.min(h, state.rows - cell.row);
    const occ = occupancy(cell);
    const valid = fits(cell.col, cell.row, w, h, occ);
    drag.dropTarget = { col: cell.col, row: cell.row, w, h, valid };
    showDrop(cell.col, cell.row, w, h, valid);
  }

  function onPointerUp(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const d = drag;
    if (!d.moved) {
      cleanupDrag();
      clearDrop();
      if (d.tap) d.tap();
      return;
    }
    const t = d.dropTarget;
    let committed = false;
    if (t && t.valid) {
      if (d.kind === 'place') committed = placeAt(d.camId, t.col, t.row, t.w);
      else if (d.kind === 'move') { d.cell.col = t.col; d.cell.row = t.row; committed = true; }
      else if (d.kind === 'resize') { d.cell.w = t.w; d.cell.h = t.h; committed = true; }
    }
    cleanupDrag();
    clearDrop();
    if (!committed) flashInvalid();   // invalid drop reverts with a red shake
    renderBuilder();
  }

  function onPointerCancel(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    cleanupDrag();
    clearDrop();
    renderBuilder();
  }

  function toggleControls(el) {
    preview.querySelectorAll('.gc-b-tile.gc-b-open').forEach((t) => { if (t !== el) t.classList.remove('gc-b-open'); });
    el.classList.toggle('gc-b-open');
  }

  // ---- Render --------------------------------------------------------------

  function renderBuilder() {
    colsVal.textContent = state.cols;
    rowsVal.textContent = state.rows;
    preview.style.setProperty('--gc-b-cols', state.cols);
    preview.style.setProperty('--gc-b-rows', state.rows);
    preview.innerHTML = '';
    dropEl = null;   // the old highlight lived under preview and was just cleared

    const occ = occupancy();
    // Placed tiles: drag the body to move, drag the corner handle to resize,
    // tap to reveal the remove button.
    for (const cell of state.cells) {
      const cam = camById(cell.camera_id);
      const el = document.createElement('div');
      el.className = 'gc-b-tile';
      el.dataset.cam = cell.camera_id;
      el.style.gridArea = `${cell.row + 1} / ${cell.col + 1} / span ${cell.h} / span ${cell.w}`;
      el.innerHTML = `<span class="gc-b-tile-name">${escapeHtml(cam ? cam.name : 'Camera')}</span>` +
        `<span class="gc-b-tile-ctrls">` +
        `<button type="button" data-act="remove" aria-label="Remove tile">&times;</button></span>` +
        `<span class="gc-b-resize" data-act="resize" aria-label="Resize tile"></span>`;

      el.addEventListener('pointerdown', (e) => {
        const act = e.target && e.target.dataset ? e.target.dataset.act : null;
        if (act === 'remove') return;   // handled by the button's click
        if (act === 'resize') { beginDrag(e, { kind: 'resize', cell, tileEl: el }); return; }
        beginDrag(e, {
          kind: 'move', cell, tileEl: el,
          label: cam ? cam.name : 'Camera',
          tap: () => toggleControls(el),
        });
      });
      const rm = el.querySelector('[data-act="remove"]');
      rm.addEventListener('click', (ev) => { ev.stopPropagation(); removeCell(cell); });
      preview.append(el);
    }
    // Empty cells fill every free slot so the grid reads as a grid and drop
    // targets are obvious while a drag is in progress.
    for (let r = 0; r < state.rows; r++) {
      for (let c = 0; c < state.cols; c++) {
        if (occ.has(`${c},${r}`)) continue;
        const el = document.createElement('div');
        el.className = 'gc-b-cell';
        el.style.gridArea = `${r + 1} / ${c + 1} / span 1 / span 1`;
        preview.append(el);
      }
    }

    renderChips();
    validate();
  }

  function renderChips() {
    chipsBox.innerHTML = '';
    const enabled = (cameraCache || []).filter((c) => c.enabled !== false);
    if (!enabled.length) {
      chipsBox.innerHTML = '<span class="text-secondary small">Add a camera first.</span>';
      return;
    }
    for (const cam of enabled) {
      const placed = state.cells.some((c) => c.camera_id === cam.id);
      const chip = document.createElement('span');
      chip.className = 'gc-chip' + (placed ? ' gc-chip-placed' : '');
      chip.textContent = cam.name;
      // An unplaced chip drags onto the grid (or taps to the first free slot).
      // A placed chip is dimmed but still drags, which moves its existing tile.
      chip.addEventListener('pointerdown', (e) => {
        if (placed) {
          const cell = state.cells.find((c) => c.camera_id === cam.id);
          if (!cell) return;
          beginDrag(e, {
            kind: 'move', cell,
            tileEl: preview.querySelector(`.gc-b-tile[data-cam="${cell.camera_id}"]`),
            label: cam.name,
          });
        } else {
          beginDrag(e, { kind: 'place', camId: cam.id, label: cam.name, tap: () => placeFirstFree(cam.id) });
        }
      });
      chipsBox.append(chip);
    }
  }

  function validate() {
    let problem = '';
    if (!nameInput.value.trim()) problem = 'Give the layout a name.';
    else if (!state.cells.length) problem = 'Place at least one camera.';
    else {
      const occ = new Set();
      for (const c of state.cells) {
        if (c.col < 0 || c.row < 0 || c.col + c.w > state.cols || c.row + c.h > state.rows) { problem = 'A tile falls outside the grid.'; break; }
        for (let cc = c.col; cc < c.col + c.w; cc++) {
          for (let rr = c.row; rr < c.row + c.h; rr++) {
            const k = `${cc},${rr}`;
            if (occ.has(k)) { problem = 'Two tiles overlap.'; }
            occ.add(k);
          }
        }
      }
    }
    msgEl.textContent = problem;
    saveBtn.disabled = !!problem;
    return !problem;
  }

  nameInput.addEventListener('input', validate);

  saveBtn.addEventListener('click', async () => {
    if (!validate()) return;
    const payload = {
      name: nameInput.value.trim(),
      cols: state.cols, rows: state.rows,
      cells: state.cells.map((c) => ({ ...c })),
    };
    if (state.editingId) payload.id = state.editingId;
    const { ok, data } = await api('POST', '/api/layouts', payload);
    if (!ok) { msgEl.textContent = (data && data.detail) || 'Could not save the layout.'; return; }
    closeBuilder();
    await loadLayouts();
    activeSel.value = data.id;
    notifyGridTabs();
    flash(saveBtn, true);
  });

  loadLayouts();
})();
