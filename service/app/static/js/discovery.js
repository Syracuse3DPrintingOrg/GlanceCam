// Camera discovery: scan the LAN, ONVIF, Reolink, and Home Assistant. Every
// path returns proposed cameras that add with one tap. Find stream asks the
// server to work out a camera's RTSP address so the user never guesses a path.
// If an endpoint is missing this degrades to a short "not available yet" note
// rather than an error. Wrapped in an IIFE: this loads alongside settings.js in
// the same global scope, and shared top-level names would stop the whole script.
(() => {
'use strict';

const statusEl = document.getElementById('gc-disc-status');
const resultsEl = document.getElementById('gc-disc-results');
const resultsWrap = document.getElementById('gc-disc-results-wrap');
const progressWrap = document.getElementById('gc-scan-progress');
const progressBar = progressWrap ? progressWrap.querySelector('.progress-bar') : null;
const escapeHtml = window.gcEscapeHtml || ((s) => String(s));
const RESULT_COLS = 6;  // keep in sync with the results table header

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

function renderProposals(list, extraCreds) {
  if (!resultsEl) return;
  if (!list.length) {
    if (!resultsEl.children.length) setStatus('Nothing new found.');
    return;
  }
  for (const prop of list) renderRow(prop, extraCreds);
  const found = resultsEl.querySelectorAll('tr:not(.gc-preview-row)').length;
  setStatus(`Found ${found}.`);
  showResults();
}

// ---- One device, one table row (plus a hidden preview row) -----------------

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
// a stream, anything else is treated as a snapshot).
async function addProposal(prop, name, url, creds, statusEl2, addBtn) {
  if (addBtn) addBtn.disabled = true;
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
  } else {
    if (addBtn) addBtn.disabled = false;
    if (statusEl2) statusEl2.textContent = (data && data.detail) || 'Could not add';
  }
  return ok;
}

function renderRow(prop, extraCreds) {
  const creds = { ...(extraCreds || {}) };
  if (!creds.credential_id) {
    if (!creds.username && prop.username) creds.username = prop.username;
    if (!creds.password && prop.password) creds.password = prop.password;
  }

  const host = hostOf(prop);
  const rtsp = hasRtsp(prop);
  const ipCell = host
    ? `<a href="http://${escapeAttr(host)}" target="_blank" rel="noopener">${escapeHtml(host)}</a>`
    : '<span class="text-secondary">unknown</span>';

  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td>${ipCell}</td>` +
    `<td>${escapeHtml(brandText(prop))}</td>` +
    `<td>${protocolBadges(prop)}</td>` +
    `<td class="text-secondary small">${escapeHtml((prop.ports || []).join(', '))}</td>` +
    `<td class="text-secondary small">${escapeHtml(detailText(prop))}</td>` +
    `<td class="text-end text-nowrap">` +
      (rtsp ? `<button class="btn btn-primary btn-sm" data-act="find">Find stream</button> ` : '') +
      `<button class="btn btn-outline-secondary btn-sm" data-act="preview">Preview</button> ` +
      `<button class="btn ${rtsp ? 'btn-outline-secondary' : 'btn-primary'} btn-sm" data-act="add">Add</button>` +
      `<div class="small gc-row-note"></div>` +
    `</td>`;

  const note = tr.querySelector('.gc-row-note');
  const previewRow = buildPreviewRow(prop, creds, note);

  tr.querySelector('[data-act="add"]').addEventListener('click', (e) => {
    const cand = candidatesFor(prop)[0];
    addProposal(prop, prop.name, cand ? cand.url : '', creds, note, e.currentTarget);
  });
  tr.querySelector('[data-act="preview"]').addEventListener('click', async () => {
    const opening = previewRow.classList.contains('d-none');
    previewRow.classList.toggle('d-none');
    if (opening) await previewRow._populate();
  });
  const findBtn = tr.querySelector('[data-act="find"]');
  if (findBtn) {
    findBtn.addEventListener('click', () => runFindStream(prop, previewRow, note, findBtn));
  }

  resultsEl.append(tr);
  resultsEl.append(previewRow);
}

// Ask the server to try the common stream paths for this host and use the first
// that works. On success the preview row is filled in with a thumbnail and an
// "Add this" button; on failure it opens for manual picking with every brand's
// addresses in the dropdown.
async function runFindStream(prop, previewRow, note, findBtn) {
  const host = hostOf(prop);
  if (!host) { note.textContent = 'No address to search.'; return; }
  findBtn.disabled = true;
  note.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Looking for the stream...';
  const creds = getActiveCreds();
  const { data } = await post('/api/discovery/find-stream', {
    host, ports: prop.ports || [], hint: prop.brand || prop.brand_hint || '',
    ...credsBody(creds),
  });
  findBtn.disabled = false;
  previewRow.classList.remove('d-none');
  await previewRow._populate();
  if (data && data.ok) {
    if (data.sub_url) prop.sub_url = data.sub_url;
    previewRow._setUrl(data.main_url);
    const res = data.resolution ? ` (${data.resolution[0]}x${data.resolution[1]})` : '';
    note.textContent = `Found ${data.label || data.brand || 'a stream'}${res}.`;
    previewRow._showThumb(data.main_url, creds);
  } else {
    note.textContent = (data && data.error) || 'No stream found. Pick one below.';
  }
}

// The expansion row: pick a URL candidate, enter or reuse credentials, test it
// (with a real thumbnail when the device can hand one back), then add.
function buildPreviewRow(prop, creds, rowNote) {
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
  tr._setUrl = (url) => {
    f('url').value = url || '';
    if (sel) {
      const match = [...sel.options].find((o) => o.value === url);
      if (match) sel.value = url;
    }
  };
  tr._showThumb = async (url, useCreds) => {
    thumbWrap.classList.add('d-none');
    const res = await previewProbe(url, useCreds || currentCreds());
    if (res.blob) {
      thumb.src = URL.createObjectURL(res.blob);
      thumbWrap.classList.remove('d-none');
    }
  };

  // The row can use its own typed login, or the saved set chosen up top.
  const currentCreds = () => {
    const active = getActiveCreds();
    if (active.credential_id) return active;
    const u = f('user').value, p = f('pass').value;
    if (u || p) return { username: u, password: p };
    return active;
  };

  tr.querySelector('[data-act="test"]').addEventListener('click', async () => {
    const url = f('url').value.trim();
    thumbWrap.classList.add('d-none');
    if (!url) { note.textContent = 'Enter an address first.'; return; }
    note.textContent = 'Testing...';
    const res = await previewProbe(url, currentCreds());
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
    if (ok && rowNote) rowNote.textContent = 'Added';
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

// ---- Network scan (polling job) --------------------------------------------

async function pollScan(jobId) {
  const r = await fetch(`/api/discovery/scan/${encodeURIComponent(jobId)}`);
  if (!r.ok) { notAvailable(r.status); return; }
  const data = await r.json();
  let pct = Number.isFinite(data.progress) ? data.progress : null;
  if (pct == null && data.progress && data.progress.total > 0) {
    pct = (100 * data.progress.done) / data.progress.total;
  }
  showProgress(pct == null ? 5 : pct);
  const status = (data.status || '').toLowerCase();
  if (['done', 'complete', 'completed', 'finished', 'error'].includes(status)) {
    showProgress(null);
    if (status === 'error') { setStatus('The scan could not finish.'); return; }
    renderProposals(proposalsOf(data), getActiveCreds());
    return;
  }
  setTimeout(() => pollScan(jobId), 1200);
}

const scanBtn = document.getElementById('gc-scan-btn');
if (scanBtn) {
  scanBtn.addEventListener('click', async () => {
    clearResults();
    setStatus('Scanning the network...');
    showProgress(3);
    const { ok, status, data } = await post('/api/discovery/scan', {});
    if (!ok || !data) { notAvailable(status); return; }
    if (data.job_id) { pollScan(data.job_id); return; }
    // Some builds may answer synchronously with results.
    showProgress(null);
    renderProposals(proposalsOf(data), getActiveCreds());
  });
}

// ---- ONVIF -----------------------------------------------------------------

const onvifBtn = document.getElementById('gc-onvif-btn');
if (onvifBtn) {
  onvifBtn.addEventListener('click', async () => {
    clearResults();
    setStatus('Searching for ONVIF cameras...');
    const { ok, status, data } = await post('/api/discovery/onvif', {});
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

// ---- Reolink ---------------------------------------------------------------

const reolinkForm = document.getElementById('gc-reolink-form');
if (reolinkForm) {
  reolinkForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearResults();
    setStatus('Asking the Reolink camera...');
    const fd = new FormData(reolinkForm);
    const body = { host: fd.get('host'), username: fd.get('username'), password: fd.get('password') };
    const { ok, status, data } = await post('/api/discovery/reolink', body);
    if (!ok || !data) { notAvailable(status); return; }
    renderProposals(proposalsOf(data), { username: body.username, password: body.password });
  });
}

// ---- Home Assistant --------------------------------------------------------

const haForm = document.getElementById('gc-ha-form');
if (haForm) {
  haForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearResults();
    setStatus('Listing Home Assistant cameras...');
    const fd = new FormData(haForm);
    const body = { base_url: fd.get('base_url') || undefined, token: fd.get('token') || undefined };
    const { ok, status, data } = await post('/api/discovery/homeassistant', body);
    if (!ok || !data) { notAvailable(status); return; }
    renderProposals(proposalsOf(data), {});
  });
}

})();
