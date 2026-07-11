// Camera discovery: scan the LAN, ONVIF, Reolink, and Home Assistant. Every
// path returns proposed cameras that add with one tap. If an endpoint is
// missing this degrades to a short "not available yet" note rather than an
// error. Wrapped in an IIFE: this loads alongside settings.js in the same
// global scope, and shared top-level names would stop the whole script.
(() => {
'use strict';

const statusEl = document.getElementById('gc-disc-status');
const resultsEl = document.getElementById('gc-disc-results');
const resultsWrap = document.getElementById('gc-disc-results-wrap');
const progressWrap = document.getElementById('gc-scan-progress');
const progressBar = progressWrap ? progressWrap.querySelector('.progress-bar') : null;
const escapeHtml = window.gcEscapeHtml || ((s) => String(s));
const RESULT_COLS = 6;  // keep in sync with the results table header

function discCreds() {
  return {
    username: (document.getElementById('gc-disc-user') || {}).value || '',
    password: (document.getElementById('gc-disc-pass') || {}).value || '',
  };
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

function detailText(prop) {
  const bits = [];
  const res = prop.main_resolution || prop.resolution;
  if (Array.isArray(res) && res.length === 2) bits.push(`${res[0]}x${res[1]}`);
  if (prop.notes) bits.push(prop.notes);
  return bits.join(' ');
}

// Candidate URLs to preview/add: what the scan already knows, plus common RTSP
// path guesses seeded by the brand hint when the device answers RTSP.
function candidatesFor(prop) {
  const seen = new Set();
  const out = [];
  const add = (label, url) => {
    if (url && !seen.has(url)) { seen.add(url); out.push({ label, url }); }
  };
  add('Snapshot', prop.snapshot_url);
  add('Main stream', prop.main_url);
  add('Sub stream', prop.sub_url);
  if (hasRtsp(prop)) {
    const host = hostOf(prop);
    if (host) {
      add('RTSP root', `rtsp://${host}:554/`);
      const brand = (prop.brand || '').toLowerCase();
      if (brand.includes('reolink')) {
        add('Reolink main', `rtsp://${host}:554/h264Preview_01_main`);
        add('Reolink sub', `rtsp://${host}:554/h264Preview_01_sub`);
      }
      if (brand.includes('hikvision')) {
        add('Hikvision main', `rtsp://${host}:554/Streaming/Channels/101`);
        add('Hikvision sub', `rtsp://${host}:554/Streaming/Channels/102`);
      }
      if (brand.includes('dahua') || brand.includes('amcrest')) {
        add('Dahua main', `rtsp://${host}:554/cam/realmonitor?channel=1&subtype=0`);
        add('Dahua sub', `rtsp://${host}:554/cam/realmonitor?channel=1&subtype=1`);
      }
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
  delete payload.brand; delete payload.notes; delete payload.ip;
  if (url) {
    if (/^rtsp:\/\//i.test(url)) payload.main_url = url;
    else payload.snapshot_url = url;
  }
  if (creds.username) payload.username = creds.username;
  if (creds.password) payload.password = creds.password;
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
  if (!creds.username && prop.username) creds.username = prop.username;
  if (!creds.password && prop.password) creds.password = prop.password;

  const host = hostOf(prop);
  const brand = prop.brand || (prop.source && prop.source !== 'manual' ? prop.source : '') || 'Unknown';
  const ipCell = host
    ? `<a href="http://${escapeAttr(host)}" target="_blank" rel="noopener">${escapeHtml(host)}</a>`
    : '<span class="text-secondary">unknown</span>';

  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td>${ipCell}</td>` +
    `<td>${escapeHtml(brand)}</td>` +
    `<td>${protocolBadges(prop)}</td>` +
    `<td class="text-secondary small">${escapeHtml((prop.ports || []).join(', '))}</td>` +
    `<td class="text-secondary small">${escapeHtml(detailText(prop))}</td>` +
    `<td class="text-end text-nowrap">` +
      `<button class="btn btn-outline-secondary btn-sm" data-act="preview">Preview</button> ` +
      `<button class="btn btn-primary btn-sm" data-act="add">Add</button>` +
      `<div class="small gc-row-note"></div>` +
    `</td>`;

  const note = tr.querySelector('.gc-row-note');
  const previewRow = buildPreviewRow(prop, creds, note);

  tr.querySelector('[data-act="add"]').addEventListener('click', (e) => {
    const cand = candidatesFor(prop)[0];
    addProposal(prop, prop.name, cand ? cand.url : '', creds, note, e.currentTarget);
  });
  tr.querySelector('[data-act="preview"]').addEventListener('click', () => {
    previewRow.classList.toggle('d-none');
  });

  resultsEl.append(tr);
  resultsEl.append(previewRow);
}

// The expansion row: pick a URL candidate, enter credentials, test it (with a
// real thumbnail when the device can hand one back), then add.
function buildPreviewRow(prop, creds, rowNote) {
  const cands = candidatesFor(prop);
  const tr = document.createElement('tr');
  tr.className = 'gc-preview-row d-none';
  const options = cands.map((c, i) =>
    `<option value="${escapeAttr(c.url)}" ${i === 0 ? 'selected' : ''}>${escapeHtml(c.label)}</option>`).join('');
  tr.innerHTML = `
    <td colspan="${RESULT_COLS}">
      <div class="gc-preview p-2">
        <div class="row g-2 align-items-end">
          <div class="col-12 col-md-3">
            <label class="form-label small mb-1">Name</label>
            <input class="form-control form-control-sm" data-f="name" value="${escapeAttr(prop.name || 'Camera')}">
          </div>
          <div class="col-12 col-md">
            <label class="form-label small mb-1">Stream or snapshot</label>
            <select class="form-select form-select-sm mb-1" data-f="candidate" ${cands.length ? '' : 'disabled'}>
              ${options || '<option>No suggestions, enter one below</option>'}
            </select>
            <input class="form-control form-control-sm" data-f="url" placeholder="rtsp://... or http://.../snap.jpg"
                   value="${escapeAttr(cands.length ? cands[0].url : '')}">
          </div>
        </div>
        <div class="row g-2 align-items-end mt-1">
          <div class="col-6 col-md-3">
            <label class="form-label small mb-1">Username</label>
            <input class="form-control form-control-sm" data-f="user" autocomplete="off" value="${escapeAttr(creds.username || '')}">
          </div>
          <div class="col-6 col-md-3">
            <label class="form-label small mb-1">Password</label>
            <input class="form-control form-control-sm" type="password" data-f="pass" autocomplete="new-password" value="${escapeAttr(creds.password || '')}">
          </div>
          <div class="col-12 col-md-auto">
            <button class="btn btn-outline-secondary btn-sm" data-act="test" type="button">Test</button>
            <button class="btn btn-primary btn-sm" data-act="add" type="button">Add this</button>
            <span class="small ms-1 gc-preview-note"></span>
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
  if (sel) sel.addEventListener('change', () => { f('url').value = sel.value; });

  const currentCreds = () => ({ username: f('user').value, password: f('pass').value });

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
      note.textContent = 'Reachable' +
        (res.data.resolution ? ` (${res.data.resolution[0]}x${res.data.resolution[1]})` : '');
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
// or JSON with the probe outcome. Never sends creds we did not collect.
async function previewProbe(url, creds) {
  try {
    const r = await fetch('/api/discovery/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, username: creds.username || '', password: creds.password || '' }),
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
    renderProposals(proposalsOf(data), discCreds());
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
    renderProposals(proposalsOf(data), discCreds());
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
    const creds = discCreds();
    const ready = list.filter((p) => p.main_url);
    const needStreams = list.filter((p) => !p.main_url && p.xaddr);
    renderProposals(ready, creds);
    for (const dev of needStreams) {
      const res = await post('/api/discovery/onvif/streams', {
        xaddr: dev.xaddr, username: creds.username, password: creds.password,
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
