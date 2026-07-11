// Functional settings page wiring for milestone 1. The UI agent will restyle
// and add drag-to-reorder and the discovery flow.

async function api(method, path, body) {
  const opts = {method, headers: {'Content-Type': 'application/json'}};
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* empty body is fine */ }
  return {ok: resp.ok, status: resp.status, data};
}

async function loadCameras() {
  const {ok, data} = await api('GET', '/api/cameras');
  const list = document.getElementById('gc-camera-list');
  if (!list) return;
  list.innerHTML = '';
  if (!ok || !Array.isArray(data) || data.length === 0) {
    list.innerHTML = '<li class="list-group-item text-secondary">No cameras yet.</li>';
    return;
  }
  for (const cam of data) {
    const li = document.createElement('li');
    li.className = 'list-group-item d-flex justify-content-between align-items-center';
    const label = document.createElement('span');
    label.textContent = cam.name + (cam.sub_url ? '' : '  (no sub stream)');
    const del = document.createElement('button');
    del.className = 'btn btn-outline-danger btn-sm';
    del.textContent = 'Remove';
    del.addEventListener('click', async () => {
      await api('DELETE', `/api/cameras/${cam.id}`);
      loadCameras();
    });
    li.append(label, del);
    list.append(li);
  }
}

function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) {
    if (v !== '') out[k] = v;
  }
  return out;
}

const addForm = document.getElementById('gc-add-camera');
if (addForm) {
  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const {ok, data} = await api('POST', '/api/cameras', formData(addForm));
    if (ok) {
      addForm.reset();
      loadCameras();
    } else {
      alert((data && data.detail) || 'Could not add that camera.');
    }
  });

  const testBtn = document.getElementById('gc-test-camera');
  testBtn.addEventListener('click', async () => {
    const result = document.getElementById('gc-test-result');
    result.textContent = 'Testing...';
    const {data} = await api('POST', '/api/cameras/test', formData(addForm));
    if (data && data.ok) {
      const res = data.resolution ? ` (${data.resolution[0]}x${data.resolution[1]})` : '';
      result.textContent = 'Reachable' + res;
    } else {
      result.textContent = (data && data.error) || 'Could not reach that camera.';
    }
  });
}

for (const id of ['gc-display-form', 'gc-access-form']) {
  const form = document.getElementById(id);
  if (!form) continue;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    // Checkboxes are absent from FormData when unchecked; send them explicitly.
    const payload = formData(form);
    form.querySelectorAll('input[type=checkbox]').forEach((cb) => {
      payload[cb.name] = cb.checked;
    });
    const {ok} = await api('POST', '/api/settings', payload);
    const btn = form.querySelector('button[type=submit]');
    const original = btn.textContent;
    btn.textContent = ok ? 'Saved' : 'Save failed';
    setTimeout(() => { btn.textContent = original; }, 1500);
  });
}

async function loadSystem() {
  const {ok, data} = await api('GET', '/api/system');
  const box = document.getElementById('gc-system');
  if (ok && data && box) {
    box.innerHTML = `<div>Version ${data.version}</div>` +
      `<div>go2rtc: ${data.go2rtc_healthy ? 'reachable' : 'unreachable'}</div>` +
      `<div>Cameras: ${data.camera_count}</div>` +
      `<div>Raspberry Pi: ${data.is_pi ? 'yes' : 'no'}</div>`;
  }
}

loadCameras();
loadSystem();
