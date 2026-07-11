// Camera discovery: scan the LAN, ONVIF, Reolink, and Home Assistant. Every
// path returns proposed cameras that add with one tap. If an endpoint is
// missing this degrades to a short "not available yet" note rather than an
// error. Wrapped in an IIFE: this loads alongside settings.js in the same
// global scope, and shared top-level names would stop the whole script.
(() => {
'use strict';

const statusEl = document.getElementById('gc-disc-status');
const resultsEl = document.getElementById('gc-disc-results');
const progressWrap = document.getElementById('gc-scan-progress');
const progressBar = progressWrap ? progressWrap.querySelector('.progress-bar') : null;
const escapeHtml = window.gcEscapeHtml || ((s) => String(s));

function discCreds() {
  return {
    username: (document.getElementById('gc-disc-user') || {}).value || '',
    password: (document.getElementById('gc-disc-pass') || {}).value || '',
  };
}

function setStatus(msg) { if (statusEl) statusEl.textContent = msg || ''; }
function clearResults() { if (resultsEl) resultsEl.innerHTML = ''; }
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
  if (!list.length) { setStatus('Nothing new found.'); return; }
  setStatus(`Found ${list.length}.`);
  for (const prop of list) renderCard(prop, extraCreds);
}

function renderCard(prop, extraCreds) {
  const col = document.createElement('div');
  col.className = 'col-12 col-sm-6';
  const src = prop.source || 'discovered';
  const hasSub = !!prop.sub_url;
  const snapOnly = !prop.main_url && (prop.snapshot_url || prop.ha_entity);
  const note = snapOnly ? 'Snapshot tile (no sub or main split)'
    : hasSub ? 'Sub and main streams' : 'Main stream only';
  col.innerHTML = `
    <div class="gc-proposal border rounded p-2 h-100">
      <input class="form-control form-control-sm mb-2" value="${escapeAttr(prop.name || 'Camera')}">
      <div class="small text-secondary mb-1">
        <span class="badge bg-dark gc-badge-dot">${escapeHtml(src)}</span> ${escapeHtml(note)}
      </div>
      <div class="small text-truncate text-secondary mb-2" title="${escapeAttr(prop.main_url || prop.snapshot_url || '')}">
        ${escapeHtml(prop.main_url || prop.snapshot_url || '')}
      </div>
      <button class="btn btn-outline-secondary btn-sm" data-act="test">Test</button>
      <button class="btn btn-primary btn-sm" data-act="add">Add</button>
      <span class="small ms-1"></span>
    </div>`;

  const nameInput = col.querySelector('input');
  const note2 = col.querySelector('span.small');
  const creds = { ...(extraCreds || {}) };
  if (!creds.username && prop.username) creds.username = prop.username;
  if (!creds.password && prop.password) creds.password = prop.password;

  col.querySelector('[data-act="test"]').addEventListener('click', async () => {
    note2.textContent = 'Testing...';
    const { data } = await post('/api/cameras/test', {
      main_url: prop.main_url, username: creds.username, password: creds.password,
    });
    if (data && data.ok) {
      note2.textContent = 'Reachable' +
        (data.resolution ? ` (${data.resolution[0]}x${data.resolution[1]})` : '');
    } else {
      note2.textContent = (data && data.error) || 'No response';
    }
  });

  col.querySelector('[data-act="add"]').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const payload = { ...prop, name: nameInput.value || prop.name || 'Camera' };
    if (creds.username) payload.username = creds.username;
    if (creds.password) payload.password = creds.password;
    const { ok, data } = await post('/api/cameras', payload);
    if (ok) {
      note2.textContent = 'Added';
      col.querySelector('.gc-proposal').classList.add('border-success');
      if (window.gcReloadCameras) window.gcReloadCameras();
    } else {
      btn.disabled = false;
      note2.textContent = (data && data.detail) || 'Could not add';
    }
  });

  resultsEl.append(col);
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
