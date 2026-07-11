# GlanceCam

GlanceCam is a quick-glance camera viewer for your local network. Point it at
the IP cameras you already own and it shows them all in one grid you can take
in at a glance. Click a camera to fill the screen, click again to go back.

That is the whole idea. GlanceCam is not an NVR: it does not record, it has no
timeline to scrub, and nothing leaves your network. It is the wall display or
the tab you keep open to see what is happening right now.

## What you get

- One live grid of all your cameras, sized to fill the screen.
- Click any tile to go fullscreen on that camera, click again to return.
- Works in any browser on your LAN, and on a Raspberry Pi wired to a display
  as a dedicated kiosk.
- Streaming handled for you by a bundled go2rtc engine (RTSP, ONVIF, and HTTP
  cameras in; WebRTC in the browser, with a snapshot fallback).
- An optional password that locks the settings and camera list while leaving
  the live grid open.

## Quickstart with Docker

You need Docker and the Compose plugin.

```bash
curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/docker-compose.prod.yml -o docker-compose.yml
docker compose up -d
```

Then open `http://YOUR-HOST:9292` in a browser and go to Settings to add your
first camera. The app runs on port 9292; go2rtc's WebRTC port (8555) is
published so browsers on your LAN can play the streams.

To build and run from a checkout of this repo instead:

```bash
docker compose up -d --build
```

## Raspberry Pi native install

Coming soon. GlanceCam will run natively on a Raspberry Pi, install its own
go2rtc engine, and optionally turn an attached display into a full-screen
kiosk. The installer and the systemd units are being finished; this section
will carry the exact steps once they land.

## Windows and other desktops

There is no Windows app to install. Any desktop is a browser client: open
`http://YOUR-HOST:9292` in Chrome, Edge, or another Chromium-based browser and
you have the grid.

To turn a Windows PC into a dedicated full-screen display, make a shortcut that
launches the browser in kiosk mode pointed at the server, for example:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk http://YOUR-HOST:9292
```

Press F11 (or Alt+F4 to close) to leave kiosk mode.

## License

MIT. See [LICENSE](LICENSE).
