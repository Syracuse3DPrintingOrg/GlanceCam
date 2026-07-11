// Camera discovery, shaped as one guided flow: type a login, press "Find my
// cameras", and each camera that answers arrives with its streams already
// worked out and a single "Add to grid" button. The network scan runs first,
// then find-stream runs for each RTSP device one at a time (so a camera is
// never hammered), and a thumbnail is fetched for the ones that resolve. ONVIF,
// Reolink, and Home Assistant sit under "More ways" for the cameras a plain
// scan misses. Everything degrades cleanly: no login still lists devices and
// offers the manual preview flow; a missing endpoint shows a short note, not an
// error. Wrapped in an IIFE because it shares the page's global scope with
// settings.js, where a stray top-level name would break both files.
(() => {
'use strict';

const statusEl = document.getElementById('gc-disc-status');
const resultsEl = document.getElementById('gc-disc-results');
const resultsWrap = document.getElementById('gc-disc-results-wrap');
const progressWrap = document.getElementById('gc-scan-progress');
const progressBar = progressWrap ? progressWrap.querySelector('.progress-bar') : null;
const escapeHtml = window.gcEscapeHtml || ((s) => String(s));
const RESULT_COLS = 6;  // keep in sync with the results table header

// What is already on the grid, refreshed before each discovery run so a device
// that is already a camera reads as "Already added" instead of offering to add
// it twice. Only id/host/main_url/ha_entity come back (no credentials).
let knownCache = [];
async function refreshKnown() {
  const r = await fetch('/api/cameras/urls');
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  knownCache = (r.ok && Array.isArray(data)) ? data : [];
}

// The login to probe with: a saved set (sent as credential_id so the browser
// never holds the password) when one is chosen, else the typed username and
// password. Settings.js fills the saved-set dropdown and disables the manual
// fields while a set is active.
function getActiveCreds() {
  const sel = document.getElementById('gc-disc-cred-select');
  if (sel && sel.value) return { credential_id: sel.value };
  return {
    username: (document.getElementById('gc-disc-user') || {}).value || '',
    password: (document.getElementById('gc-disc-pass') || {}).value || '',
  };
}
function hasLogin(creds) {
  return !!(creds && (creds.credential_id || creds.username));
}

// The credential fields to put in a request body, from whatever getActiveCreds
// (or a per-row override) produced.
function credsBody(creds) {
  if (!creds) return {};
  if (creds.credential_id) return { credential_id: creds.credential_id };
  const out = {};
  if (creds.username) out.username = creds.username;
  if (creds.password) out.password = creds.password;
  return out;
}

function applyCredsToPayload(payload, creds) {
  Object.assign(payload, credsBody(creds));
}

function setStatus(msg) { if (statusEl) statusEl.textContent = msg || ''; }
function clearResults() {
  if (resultsEl) resultsEl.innerHTML = '';
  if (resultsWrap) resultsWrap.classList.add('d-none');
}
function showResults() {
  if (resultsWrap && resultsEl) {
    resultsWrap.classList.toggle('d-none', resultsEl.children.length === 0);
  }
}
function showProgress(pct) {
  if (!progressWrap) return;
  progressWrap.classList.toggle('d-none', pct == null);
  if (progressBar && pct != null) progressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

// A button that shows it is working: disabled with a new label, restored later.
function setBusy(btn, label) {
  if (!btn) return;
  if (label != null) {
    if (btn.dataset.orig === undefined) btn.dataset.orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
  } else {
    btn.disabled = false;
    if (btn.dataset.orig !== undefined) { btn.textContent = btn.dataset.orig; delete btn.dataset.orig; }
  }
}

async function post(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null };
  }
}

// Pull a list of proposal dicts out of whatever shape an endpoint returns.
function proposalsOf(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  if (data && Array.isArray(data.proposals)) return data.proposals;
  if (data && Array.isArray(data.cameras)) return data.cameras;
  // A finished scan job nests its findings: {results: {cameras: [...]}}.
  if (data && data.results && typeof data.results === 'object') return proposalsOf(data.results);
  return [];
}

// ---- Matching a device to a camera already on the grid ---------------------

// The path part of a stream/snapshot URL, trailing slashes trimmed, for
// comparing two URLs on the same host.
function pathOf(url) {
  const m = /^[a-z]+:\/\/[^/]+(\/[^?#]*)/i.exec(url || '');
  return m ? m[1].replace(/\/+$/, '') : '';
}

// True when a proposal points at a device already added. Matches a Home
// Assistant entity exactly, otherwise the host (and, when the proposal already
// carries a stream URL, the path too, so a second channel on the same host is
// not mistaken for the first).
function alreadyAdded(prop) {
  if (prop.ha_entity && knownCache.some((c) => c.ha_entity && c.ha_entity === prop.ha_entity)) {
    return true;
  }
  const host = hostOf(prop);
  if (!host) return false;
  const url = prop.main_url || prop.snapshot_url || '';
  const path = pathOf(url);
  return knownCache.some((c) => {
    if (!c.host || c.host !== host) return false;
    if (!url || !c.main_url) return true;             // same host, no path to compare
    return pathOf(c.main_url) === path;
  });
}

// ---- Reading a proposal ----------------------------------------------------

// The IP the row links to. Scan proposals carry ``ip``; ONVIF/Reolink/HA ones
// only carry stream URLs, so fall back to the host inside the first URL we have.
function hostOf(prop) {
  if (prop.ip) return prop.ip;
  const url = prop.xaddr || prop.main_url || prop.sub_url || prop.snapshot_url || '';
  const m = /^[a-z]+:\/\/([^/:?#]+)/i.exec(url);
  return m ? m[1] : '';
}

function hasRtsp(prop) {
  if (prop.rtsp) return true;
  const ports = prop.ports || [];
  if (ports.includes(554) || ports.includes(8554)) return true;
  return /^rtsp:\/\//i.test(prop.main_url || '') || /^rtsp:\/\//i.test(prop.sub_url || '');
}

function hasHttp(prop) {
  const ports = prop.ports || [];
  if (ports.some((p) => [80, 88, 8000, 8080].includes(p))) return 'HTTP';
  if (ports.some((p) => [443, 8443].includes(p))) return 'HTTPS';
  const u = prop.snapshot_url || prop.main_url || '';
  if (/^https:\/\//i.test(u)) return 'HTTPS';
  if (/^http:\/\//i.test(u)) return 'HTTP';
  return '';
}

function protocolBadges(prop) {
  const out = [];
  if (hasRtsp(prop)) out.push(['secondary', 'RTSP']);
  const http = hasHttp(prop);
  if (http) out.push(['secondary', http]);
  if (prop.snapshot_url) out.push(['info', 'Snapshot']);
  if (prop.ha_entity) out.push(['secondary', 'Home Assistant']);
  if (prop.auth_required) out.push(['warning', 'login required']);
  if (!out.length) out.push(['secondary', prop.source || 'unknown']);
  return out.map(([c, t]) =>
    `<span class="badge bg-${c} gc-badge-dot me-1">${escapeHtml(t)}</span>`).join('');
}

// The brand column text. A confirmed brand shows plainly; a port-signature guess
// shows with a question mark so it never reads as certain.
function brandText(prop) {
  if (prop.brand) return prop.brand;
  if (prop.brand_hint) return `${prop.brand_hint}?`;
  if (prop.source && prop.source !== 'manual') return prop.source;
  return 'Unknown';
}

function detailText(prop) {
  const bits = [];
  const res = prop.main_resolution || prop.resolution;
  if (Array.isArray(res) && res.length === 2) bits.push(`${res[0]}x${res[1]}`);
  if (prop.notes) bits.push(prop.notes);
  return bits.join(' ');
}

// The editable name a found camera arrives with; the user can change it before
// adding.
function resolvedName(prop) {
  if (prop.name) return prop.name;
  const host = hostOf(prop);
  return host ? `Camera at ${host}` : 'Camera';
}

// The URLs already known for a device: what a probe returned, no guessing. The
// full brand list is added to the preview dropdown separately (loadAllCandidates).
function candidatesFor(prop) {
  const seen = new Set();
  const out = [];
  const add = (label, url) => {
    if (url && !seen.has(url)) { seen.add(url); out.push({ label, url }); }
  };
  add('Snapshot', prop.snapshot_url);
  add('Main stream', prop.main_url);
  add('Sub stream', prop.sub_url);
  return out;
}

// The known URLs plus every major brand's default addresses (from the server, so
// the dropdown always offers a path even when the brand was not detected).
async function loadAllCandidates(prop) {
  const base = candidatesFor(prop);
  const host = hostOf(prop);
  if (!host) return base;
  const hint = prop.brand || prop.brand_hint || '';
  let data = null;
  try {
    const r = await fetch(`/api/discovery/stream-paths?host=${encodeURIComponent(host)}` +
      `&hint=${encodeURIComponent(hint)}`);
    if (r.ok) data = await r.json();
  } catch (e) { /* offline: fall back to the known URLs only */ }
  if (!data || !Array.isArray(data.candidates)) return base;
  const seen = new Set(base.map((c) => c.url));
  const out = base.slice();
  for (const c of data.candidates) {
    if (c.main_url && !seen.has(c.main_url)) {
      seen.add(c.main_url); out.push({ label: `${c.label} main`, url: c.main_url });
    }
    if (c.sub_url && !seen.has(c.sub_url)) {
      seen.add(c.sub_url); out.push({ label: `${c.label} sub`, url: c.sub_url });
    }
  }
  return out;
}

// Save a proposal, mapping the chosen URL to the right field (an rtsp:// URL is
// a stream, anything else is treated as a snapshot). Refreshes the grid and the
// already-added map on success. Returns whether it saved.
async function addProposal(prop, name, url, creds, statusEl2, addBtn) {
  if (addBtn) setBusy(addBtn, 'Adding...');
  const payload = { ...prop, name: name || prop.name || 'Camera' };
  delete payload.ports; delete payload.rtsp; delete payload.auth_required;
  delete payload.brand; delete payload.brand_hint; delete payload.notes; delete payload.ip;
  if (url) {
    if (/^rtsp:\/\//i.test(url)) payload.main_url = url;
    else payload.snapshot_url = url;
  }
  applyCredsToPayload(payload, creds);
  const { ok, data } = await post('/api/cameras', payload);
  if (ok) {
    if (statusEl2) statusEl2.textContent = 'Added';
    if (window.gcReloadCameras) window.gcReloadCameras();
    await refreshKnown();
  } else {
    if (addBtn) setBusy(addBtn, null);
    if (statusEl2) statusEl2.textContent = (data && data.detail) || 'Could not add';
  }
  return ok;
}

// ---- One device, one row (plus a hidden preview/details row) ---------------

const STATE_NOTES = {
  'needs-login': 'Needs a login. Add one above, then find it again.',
  'login-refused': 'The login was refused: check the password.',
  'could-not': 'Could not auto-detect. Open Details to pick an address.',
  'pending': 'Ready to check.',
  'manual': 'Open Details to preview and add it.',
};

function viewGridLink() {
  return `<a class="small" href="/">View grid</a>`;
}

// Build a row and return a small controller so the finder can drive it through
// its states (checking, ready, needs-login, and so on) as it works.
function makeRow(prop, extraCreds) {
  const creds = { ...(extraCreds || {}) };
  if (!creds.credential_id) {
    if (!creds.username && prop.username) creds.username = prop.username;
    if (!creds.password && prop.password) creds.password = prop.password;
  }

  const host = hostOf(prop);
  const isRtsp = hasRtsp(prop);
  const already = alreadyAdded(prop);
  const ipCell = host
    ? `<a href="http://${escapeAttr(host)}" target="_blank" rel="noopener">${escapeHtml(host)}</a>`
    : '<span class="text-secondary">unknown</span>';

  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td>${ipCell}</td>` +
    `<td>${escapeHtml(brandText(prop))}</td>` +
    `<td>${protocolBadges(prop)}</td>` +
    `<td class="text-secondary small">${escapeHtml((prop.ports || []).join(', '))}</td>` +
    `<td class="gc-row-detail text-secondary small">` +
      `<div class="gc-row-thumb d-none mb-1"><img alt="Camera preview"></div>` +
      `<div>${escapeHtml(detailText(prop))}</div>` +
    `</td>` +
    `<td class="text-end gc-row-add">` +
      `<div class="gc-row-actions d-flex flex-column align-items-end gap-1"></div>` +
      `<div class="small gc-row-note mt-1"></div>` +
    `</td>`;

  const actions = tr.querySelector('.gc-row-actions');
  const note = tr.querySelector('.gc-row-note');
  const thumbWrap = tr.querySelector('.gc-row-thumb');
  const thumb = thumbWrap.querySelector('img');

  const previewRow = buildPreviewRow(prop, creds, note, () => controller.setState('added'));

  function openDetails() {
    const opening = previewRow.classList.contains('d-none');
    previewRow.classList.toggle('d-none', !opening);
    if (opening) previewRow._populate();
  }

  async function showThumb(url, useCreds) {
    thumbWrap.classList.add('d-none');
    const res = await previewProbe(url, useCreds);
    if (res.blob) {
      thumb.src = URL.createObjectURL(res.blob);
      thumbWrap.classList.remove('d-none');
    }
  }

  function setState(state, info) {
    info = info || {};
    actions.innerHTML = '';

    if (state === 'already' || state === 'added') {
      note.textContent = state === 'added' ? 'On the grid.' : 'Already added.';
      actions.innerHTML = viewGridLink();
      return;
    }

    if (state === 'checking') {
      note.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' +
        'Looking for its streams...';
      return;
    }

    if (state === 'ready' || state === 'snapshot') {
      const url = state === 'ready' ? prop.main_url : prop.snapshot_url;
      note.textContent = info.note ||
        (state === 'ready' ? 'Found its streams.' : 'Has a snapshot.');
      actions.innerHTML =
        `<input class="form-control form-control-sm gc-row-name" ` +
          `value="${escapeAttr(resolvedName(prop))}" aria-label="Camera name">` +
        `<div class="d-flex gap-1">` +
          `<button class="btn btn-primary btn-sm" data-a="add">Add to grid</button>` +
          `<button class="btn btn-outline-secondary btn-sm" data-a="details">Details</button>` +
        `</div>`;
      const nameEl = actions.querySelector('.gc-row-name');
      actions.querySelector('[data-a="add"]').addEventListener('click', async (e) => {
        const ok = await addProposal(prop, nameEl.value, url, getActiveCreds(), null, e.currentTarget);
        if (ok) { setState('added'); maybeSaveLogin(); }
        else { note.textContent = 'Could not add it. Open Details to check the address.'; }
      });
      actions.querySelector('[data-a="details"]').addEventListener('click', openDetails);
      return;
    }

    // pending / needs-login / login-refused / could-not / manual
    note.textContent = info.note || info.error || STATE_NOTES[state] || '';
    const parts = [];
    if (isRtsp) parts.push(`<button class="btn btn-outline-primary btn-sm" data-a="find">Find stream</button>`);
    parts.push(`<button class="btn btn-outline-secondary btn-sm" data-a="details">Details</button>`);
    actions.innerHTML = `<div class="d-flex gap-1">${parts.join('')}</div>`;
    const findBtn = actions.querySelector('[data-a="find"]');
    if (findBtn) findBtn.addEventListener('click', () => autoFind(controller, getActiveCreds(), findBtn));
    actions.querySelector('[data-a="details"]').addEventListener('click', openDetails);
  }

  const controller = { tr, previewRow, prop, host, isRtsp, alreadyAdded: already, setState, showThumb };

  if (already) setState('already');
  else if (prop.main_url) setState('ready', { note: 'Ready to add.' });
  else if (isRtsp) setState('pending');
  else if (prop.snapshot_url) setState('snapshot');
  else setState('manual');

  resultsEl.append(tr);
  resultsEl.append(previewRow);
  return controller;
}

// Ask the server to work out this device's stream from the common brand paths,
// using the given login. On success the row flips to "ready" with a thumbnail
// and an "Add to grid" button; on failure it explains the specific next step.
async function autoFind(row, creds, btn) {
  const prop = row.prop;
  const host = row.host || hostOf(prop);
  if (!host) { row.setState('could-not'); return false; }
  if (btn) setBusy(btn, 'Looking...');
  row.setState('checking');
  const { data } = await post('/api/discovery/find-stream', {
    host, ports: prop.ports || [], hint: prop.brand || prop.brand_hint || '',
    ...credsBody(creds),
  });
  if (btn) setBusy(btn, null);

  if (data && data.ok) {
    prop.main_url = data.main_url;
    if (data.sub_url) prop.sub_url = data.sub_url;
    const res = data.resolution ? ` (${data.resolution[0]}x${data.resolution[1]})` : '';
    const label = data.label || data.brand || 'a stream';
    row.setState('ready', { note: `Found ${label}${res}.` });
    row.showThumb(data.main_url, creds);
    maybeSaveLogin();
    return true;
  }

  if (!hasLogin(creds)) { row.setState('needs-login'); return false; }
  const authish = prop.auth_required ||
    (data && /login|password|auth|credential/i.test(data.error || ''));
  row.setState(authish ? 'login-refused' : 'could-not', { error: data && data.error });
  return false;
}

// The expansion row: pick a URL candidate, enter or reuse credentials, test it
// (with a real thumbnail when the device can hand one back), then add.
function buildPreviewRow(prop, creds, rowNote, onAdded) {
  const tr = document.createElement('tr');
  tr.className = 'gc-preview-row d-none';
  tr.innerHTML = `
    <td colspan="${RESULT_COLS}">
      <div class="gc-preview p-3">
        <div class="row g-3 align-items-end">
          <div class="col-12 col-lg-3">
            <label class="form-label small mb-1">Name</label>
            <input class="form-control form-control-sm" data-f="name" value="${escapeAttr(prop.name || 'Camera')}">
          </div>
          <div class="col-12 col-lg-5">
            <label class="form-label small mb-1">Stream or snapshot</label>
            <select class="form-select form-select-sm mb-1" data-f="candidate">
              <option>Loading addresses...</option>
            </select>
            <input class="form-control form-control-sm" data-f="url" placeholder="rtsp://... or http://.../snap.jpg">
          </div>
          <div class="col-6 col-lg-2">
            <label class="form-label small mb-1">Username</label>
            <input class="form-control form-control-sm gc-cred-user" data-f="user" autocomplete="off" value="${escapeAttr(creds.username || '')}">
          </div>
          <div class="col-6 col-lg-2">
            <label class="form-label small mb-1">Password</label>
            <input class="form-control form-control-sm gc-cred-pass" type="password" data-f="pass" autocomplete="new-password" value="${escapeAttr(creds.password || '')}">
          </div>
        </div>
        <div class="row g-2 mt-1">
          <div class="col-12">
            <button class="btn btn-outline-secondary btn-sm" data-act="test" type="button">Test</button>
            <button class="btn btn-primary btn-sm" data-act="add" type="button">Add this</button>
            <span class="small ms-2 gc-preview-note"></span>
          </div>
        </div>
        <div class="gc-preview-thumb mt-2 d-none"><img alt="Camera preview"></div>
      </div>
    </td>`;

  const f = (name) => tr.querySelector(`[data-f="${name}"]`);
  const note = tr.querySelector('.gc-preview-note');
  const thumbWrap = tr.querySelector('.gc-preview-thumb');
  const thumb = thumbWrap.querySelector('img');
  const sel = f('candidate');
  if (sel) sel.addEventListener('change', () => { if (sel.value) f('url').value = sel.value; });

  let populated = false;
  tr._populate = async () => {
    if (populated) return;
    populated = true;
    const cands = await loadAllCandidates(prop);
    sel.innerHTML = cands.length
      ? cands.map((c, i) =>
          `<option value="${escapeAttr(c.url)}" ${i === 0 ? 'selected' : ''}>${escapeHtml(c.label)}</option>`).join('')
      : '<option value="">No suggestions, enter one below</option>';
    if (cands.length && !f('url').value) f('url').value = cands[0].url;
  };

  // The row can use its own typed login, or the saved set chosen up top.
  const currentCreds = () => {
    const active = getActiveCreds();
    if (active.credential_id) return active;
    const u = f('user').value, p = f('pass').value;
    if (u || p) return { username: u, password: p };
    return active;
  };

  tr.querySelector('[data-act="test"]').addEventListener('click', async (e) => {
    const url = f('url').value.trim();
    thumbWrap.classList.add('d-none');
    if (!url) { note.textContent = 'Enter an address first.'; return; }
    setBusy(e.currentTarget, 'Testing...');
    const res = await previewProbe(url, currentCreds());
    setBusy(e.currentTarget, null);
    if (res.blob) {
      thumb.src = URL.createObjectURL(res.blob);
      thumbWrap.classList.remove('d-none');
      note.textContent = 'Snapshot loaded.';
    } else if (res.data && res.data.ok) {
      const bits = [];
      if (res.data.codec) bits.push(res.data.codec);
      if (res.data.resolution) bits.push(`${res.data.resolution[0]}x${res.data.resolution[1]}`);
      note.textContent = `Stream works${bits.length ? ` (${bits.join(', ')})` : ''}.`;
    } else {
      note.textContent = (res.data && res.data.error) || 'No response.';
    }
  });

  tr.querySelector('[data-act="add"]').addEventListener('click', async (e) => {
    const ok = await addProposal(prop, f('name').value, f('url').value.trim(),
      currentCreds(), note, e.currentTarget);
    if (ok) {
      maybeSaveLogin();
      if (onAdded) onAdded();
      else if (rowNote) rowNote.textContent = 'Added';
    }
  });

  return tr;
}

// POST /api/discovery/preview returns either an image body (a real thumbnail)
// or JSON with the probe outcome. Sends a saved-set id or only the creds typed.
async function previewProbe(url, creds) {
  try {
    const r = await fetch('/api/discovery/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, ...credsBody(creds) }),
    });
    const ctype = (r.headers.get('content-type') || '').toLowerCase();
    if (ctype.startsWith('image/')) return { blob: await r.blob() };
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    return { data };
  } catch (e) {
    return { data: null };
  }
}

function escapeAttr(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function notAvailable(status) {
  showProgress(null);
  setStatus(status === 404 || status === 0
    ? 'This search is not available in this build yet.'
    : 'That search could not run.');
}

// ---- Save the login on first success ---------------------------------------

// Until the first login is saved, offer a one-tap "save this for next time".
// Show it only when nothing is saved yet and a typed (not saved) login is in
// use, so it never clutters a set-up device.
let loginSaved = false;
function updateSaveLoginVisibility() {
  const row = document.getElementById('gc-save-login-row');
  if (!row) return;
  const count = window.gcCredentialCount ? window.gcCredentialCount() : 0;
  const sel = document.getElementById('gc-disc-cred-select');
  const usingSaved = !!(sel && sel.value);
  row.classList.toggle('d-none', loginSaved || count > 0 || usingSaved);
}

// After the first camera resolves or is added with a typed login, save it as a
// named set if the user asked, so the next scan does not need it re-typed.
async function maybeSaveLogin() {
  if (loginSaved) return;
  const chk = document.getElementById('gc-save-login');
  if (!chk || !chk.checked) return;
  const creds = getActiveCreds();
  if (creds.credential_id || !creds.username) return;  // only a typed login is savable
  const nameEl = document.getElementById('gc-save-login-name');
  const name = (nameEl && nameEl.value.trim()) || 'Camera login';
  const { ok } = await post('/api/credentials', {
    name, username: creds.username, password: creds.password,
  });
  if (ok) {
    loginSaved = true;
    chk.checked = false;
    if (window.gcReloadCredentials) window.gcReloadCredentials();
    updateSaveLoginVisibility();
  }
}

document.addEventListener('gc-creds-changed', updateSaveLoginVisibility);
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'gc-disc-cred-select') updateSaveLoginVisibility();
});
updateSaveLoginVisibility();

// ---- Rendering a batch of proposals (ONVIF / Reolink / HA) ------------------

function renderProposals(list, extraCreds) {
  if (!list.length) {
    if (!resultsEl.children.length) setStatus('Nothing new found.');
    return;
  }
  for (const prop of list) makeRow(prop, extraCreds);
  const found = resultsEl.querySelectorAll('tr:not(.gc-preview-row)').length;
  setStatus(`Found ${found}.`);
  showResults();
}

// ---- The network scan (a polling job) --------------------------------------

// Run a scan to completion and resolve with its proposals (or null on failure).
function runScan() {
  return new Promise((resolve) => {
    (async () => {
      const { ok, status, data } = await post('/api/discovery/scan', {});
      if (!ok || !data) { notAvailable(status); resolve(null); return; }
      if (!data.job_id) { showProgress(null); resolve(proposalsOf(data)); return; }
      const jobId = data.job_id;
      const tick = async () => {
        const r = await fetch(`/api/discovery/scan/${encodeURIComponent(jobId)}`);
        if (!r.ok) { notAvailable(r.status); resolve(null); return; }
        const d = await r.json();
        let pct = Number.isFinite(d.progress) ? d.progress : null;
        if (pct == null && d.progress && d.progress.total > 0) {
          pct = (100 * d.progress.done) / d.progress.total;
        }
        showProgress(pct == null ? 5 : pct);
        const st = (d.status || '').toLowerCase();
        if (['done', 'complete', 'completed', 'finished', 'error'].includes(st)) {
          showProgress(null);
          if (st === 'error') { setStatus('The scan could not finish.'); resolve(null); return; }
          resolve(proposalsOf(d));
          return;
        }
        setTimeout(tick, 1200);
      };
      tick();
    })();
  });
}

// The one primary flow: scan, then auto-detect each RTSP camera's stream one at
// a time with the active login, so found cameras arrive ready to add.
async function findMyCameras(btn) {
  clearResults();
  await refreshKnown();
  setStatus('Looking for cameras on your network...');
  showProgress(3);
  setBusy(btn, 'Searching...');
  const proposals = await runScan();
  setBusy(btn, null);
  if (!proposals) return;
  if (!proposals.length) {
    setStatus('No cameras answered on your network. You can add one by address below.');
    return;
  }

  const creds = getActiveCreds();
  const login = hasLogin(creds);
  const rows = proposals.map((p) => makeRow(p, creds));
  showResults();
  setStatus(login
    ? `Found ${rows.length}. Checking each one for its streams...`
    : `Found ${rows.length}. Add a login above, then find them again to auto-detect streams.`);

  // Sequential on purpose: probing cameras one at a time avoids tripping a
  // camera's connection-limit lockout.
  let ready = 0;
  for (const row of rows) {
    if (row.alreadyAdded || row.prop.main_url || !row.isRtsp) continue;
    if (!login) { row.setState('needs-login'); continue; }
    if (await autoFind(row, creds)) ready++;
  }
  if (login) {
    setStatus(`Found ${rows.length}. ${ready} ready to add` +
      (ready < rows.length ? ', the rest need a look under Details.' : '.'));
  }
}

const scanBtn = document.getElementById('gc-scan-btn');
if (scanBtn) {
  scanBtn.addEventListener('click', () => findMyCameras(scanBtn));
}

// ---- More ways: ONVIF, Reolink, Home Assistant -----------------------------

const onvifBtn = document.getElementById('gc-onvif-btn');
if (onvifBtn) {
  onvifBtn.addEventListener('click', async () => {
    clearResults();
    await refreshKnown();
    setStatus('Searching for ONVIF cameras...');
    setBusy(onvifBtn, 'Searching...');
    const { ok, status, data } = await post('/api/discovery/onvif', {});
    setBusy(onvifBtn, null);
    if (!ok || !data) { notAvailable(status); return; }
    const list = proposalsOf(data);
    // Devices that came back without stream URLs need credentials to resolve;
    // fetch those per device with the shared credentials.
    const creds = getActiveCreds();
    const ready = list.filter((p) => p.main_url);
    const needStreams = list.filter((p) => !p.main_url && p.xaddr);
    renderProposals(ready, creds);
    for (const dev of needStreams) {
      const res = await post('/api/discovery/onvif/streams', {
        xaddr: dev.xaddr, ...credsBody(creds),
      });
      if (res.ok && res.data) renderProposals(proposalsOf(res.data).length
        ? proposalsOf(res.data) : [res.data], creds);
    }
    if (!ready.length && !needStreams.length) setStatus('No ONVIF cameras answered.');
  });
}

const reolinkForm = document.getElementById('gc-reolink-form');
if (reolinkForm) {
  reolinkForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearResults();
    await refreshKnown();
    setStatus('Asking the Reolink camera...');
    const fd = new FormData(reolinkForm);
    const body = { host: fd.get('host'), username: fd.get('username'), password: fd.get('password') };
    const { ok, status, data } = await post('/api/discovery/reolink', body);
    if (!ok || !data) { notAvailable(status); return; }
    renderProposals(proposalsOf(data), { username: body.username, password: body.password });
  });
}

const haForm = document.getElementById('gc-ha-form');
if (haForm) {
  haForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearResults();
    await refreshKnown();
    setStatus('Listing Home Assistant cameras...');
    const fd = new FormData(haForm);
    const body = { base_url: fd.get('base_url') || undefined, token: fd.get('token') || undefined };
    const { ok, status, data } = await post('/api/discovery/homeassistant', body);
    if (!ok || !data) { notAvailable(status); return; }
    renderProposals(proposalsOf(data), {});
  });
}

})();
