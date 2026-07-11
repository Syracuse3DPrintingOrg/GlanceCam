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
