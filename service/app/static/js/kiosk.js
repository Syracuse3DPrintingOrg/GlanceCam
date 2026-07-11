// Kiosk behaviors apply only when the page is loaded on loopback (the device's
// own display) AND the server reports Raspberry Pi hardware. A remote browser
// never inherits kiosk chrome, per the design. For milestone 1 this hides the
// settings gear and the cursor; auto-fullscreen and rotation land with the
// full kiosk work.
(function () {
  const host = location.hostname;
  const isLoopback = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
  const isPi = document.body.dataset.isPi === 'true';
  if (isLoopback && isPi) {
    document.body.classList.add('kiosk');
  }
})();
