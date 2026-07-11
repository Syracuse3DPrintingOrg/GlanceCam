// Minimal grid renderer for milestone 1. The UI agent will replace the layout
// math and add paused/snapshot tiles and the resource budget. For now: put a
// go2rtc <video-stream> in every tile playing the sub stream (falling back to
// main), lay the tiles out in a roughly square grid, and toggle fullscreen on
// click (swapping to the main stream when the tile asks for it).

const grid = document.getElementById('gc-grid');
if (grid) {
  const go2rtcPath = document.body.dataset.go2rtcPath || '/go2rtc';
  const tiles = Array.from(grid.querySelectorAll('.gc-tile'));

  // Roughly square grid: columns = ceil(sqrt(n)).
  const n = tiles.length;
  const cols = n ? Math.ceil(Math.sqrt(n)) : 1;
  grid.style.setProperty('--gc-cols', cols);

  // Build the ws src for a given stream name through the app's go2rtc proxy.
  function streamSrc(name) {
    // video-stream resolves a relative "src" against this base + /api/ws.
    return `${go2rtcPath}/api/ws?src=${encodeURIComponent(name)}`;
  }

  for (const tile of tiles) {
    const id = tile.dataset.cameraId;
    const hasSub = tile.dataset.hasSub === 'true';
    const gridName = hasSub ? `${id}_sub` : `${id}_main`;

    const el = document.createElement('video-stream');
    // The go2rtc element accepts a "src" that is a ws URL to /api/ws?src=NAME.
    // It also accepts a "mode" preference (webrtc first, then MSE, then MJPEG).
    el.setAttribute('mode', 'webrtc,mse,mjpeg');
    el.src = streamSrc(gridName);
    tile.insertBefore(el, tile.firstChild);
    tile._streamEl = el;
    tile._gridName = gridName;
    tile._mainName = `${id}_main`;
  }

  const fullscreenDefault = document.body.dataset.fullscreenMain === 'true';

  grid.addEventListener('click', (e) => {
    const tile = e.target.closest('.gc-tile');
    if (!tile || !tile._streamEl) return;

    const goingFull = !tile.classList.contains('fullscreen');
    // Only one tile is fullscreen at a time.
    for (const t of tiles) t.classList.remove('fullscreen');

    if (goingFull) {
      tile.classList.add('fullscreen');
      const useMain = (tile.dataset.fullscreenMain || String(fullscreenDefault)) === 'true';
      const wanted = useMain ? tile._mainName : tile._gridName;
      if (tile._streamEl.src !== streamSrc(wanted)) {
        tile._streamEl.src = streamSrc(wanted);
      }
    } else {
      // Back to the grid: return to the light sub stream.
      if (tile._streamEl.src !== streamSrc(tile._gridName)) {
        tile._streamEl.src = streamSrc(tile._gridName);
      }
    }
  });
}
