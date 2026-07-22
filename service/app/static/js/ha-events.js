// On-screen Home Assistant event channel (GlanceCam).
//
// On the kiosk grid page this polls /events/poll and acts on what Home
// Assistant pushed:
//   - camera -> pop that camera fullscreen on the grid (grid.js handles the
//               'gc:popup-camera' event this dispatches)
//   - notify -> a brief toast, reusing the grid's own #gc-toast
//
// It reads the current last id first, so a page opened long after an event does
// not replay a backlog. It runs only where there is a grid to act on, and stops
// cheaply if the endpoint is not present (a 404), so it costs nothing on a build
// without the events router. Everything lives in an IIFE so it leaks no globals
// that could collide with grid.js or menu.js.
(function () {
  // Only the grid page has a surface for these events; settings/login do not.
  var grid = document.getElementById('gc-grid');
  if (!grid) return;

  var POLL_MS = 2000;
  var lastId = 0;
  var stopped = false;
  var toastEl = document.getElementById('gc-toast');
  var toastTID = 0;

  function showToast(msg) {
    if (!toastEl || !msg) return;
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTID);
    toastTID = setTimeout(function () { toastEl.classList.remove('show'); }, 4000);
  }

  function handle(ev) {
    if (!ev) return;
    if (ev.type === 'camera' && ev.camera_id) {
      window.dispatchEvent(new CustomEvent('gc:popup-camera', {
        detail: { cameraId: ev.camera_id, seconds: ev.seconds }
      }));
    } else if (ev.type === 'notify') {
      showToast(ev.message || '');
    }
  }

  function apply(d) {
    if (!d) return;
    if (typeof d.last_id === 'number') lastId = Math.max(lastId, d.last_id);
    (d.events || []).forEach(handle);
  }

  function next() { if (!stopped) setTimeout(poll, POLL_MS); }

  function poll() {
    if (stopped) return;
    // A hidden tab has no screen to act on; re-check slowly. Events are keyed by
    // id, so anything sent while hidden still shows on the next visible poll.
    if (document.hidden) { setTimeout(poll, POLL_MS); return; }
    fetch('/events/poll?since=' + lastId, { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 404) { stopped = true; return null; }  // channel absent: stop
        return r.ok ? r.json() : null;
      })
      .then(function (d) { if (d) apply(d); })
      .catch(function () { /* a blip: try again next tick */ })
      .finally(next);
  }

  // Read the current last id first (a huge since), then start the 2s loop.
  fetch('/events/poll?since=999999999', { cache: 'no-store' })
    .then(function (r) {
      if (r.status === 404) { stopped = true; return null; }
      return r.ok ? r.json() : null;
    })
    .then(function (d) { if (d && typeof d.last_id === 'number') lastId = d.last_id; })
    .catch(function () {})
    .finally(next);
})();
