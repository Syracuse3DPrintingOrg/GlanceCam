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

GlanceCam can also run directly on a Raspberry Pi, no Docker required. Flash
Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager, boot it, SSH in, and
run:

```bash
curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/install.sh | sudo bash
```

That one line is the whole install: it fetches GlanceCam, sets up Python, the
app, and its own go2rtc streaming engine as systemd services, and serves the
grid on port 9292, same as the Docker install. If it finds a display attached
to the Pi, it offers to turn the Pi into a dedicated camera kiosk: a
full-screen Chromium window with no address bar, no settings gear, and no way
to leave the grid by accident. Say no (or unplug the display) and the Pi just
runs the server; any browser on the LAN still works.

Prefer to read a script before running it as root, which is always a
reasonable instinct: download it first, then run it locally instead of
piping.

```bash
curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/install.sh -o install.sh
less install.sh
sudo bash install.sh
```

Piping the one-liner never prompts for anything (there is nothing to type
into a pipe); it picks sensible defaults and installs the kiosk automatically
when a display is attached. To choose explicitly instead, set an environment
variable or pass a flag:

```bash
curl -fsSL .../install.sh | sudo GLANCECAM_ROTATION=90 bash
# or, from a local copy:
sudo bash install.sh --rotation 90 --kiosk true
```

See `install.sh --help` for the full list of flags and environment
variables (`GLANCECAM_MODE`, `GLANCECAM_KIOSK`, `GLANCECAM_ROTATION`).

## Run GlanceCam on a Windows PC

A Windows PC can host GlanceCam on its own, no Raspberry Pi and no server.
Download **GlanceCam-Setup.exe** from the
[latest release](https://github.com/Syracuse3DPrintingOrg/GlanceCam/releases),
run it, and GlanceCam appears in your Start Menu and system tray. Right-click
the tray icon to open the camera grid, start or stop GlanceCam, or have it start
when you sign in. When it finishes, open `http://localhost:9292` and add your
first camera. To remove it later, use Programs and Features like any other app.

Your cameras and settings live in `C:\ProgramData\GlanceCam` and are kept when
you uninstall (delete that folder by hand if you want them gone). See
[scripts/windows/README.md](scripts/windows/README.md) for more.

### Headless install (PowerShell)

For a server with no one signed in, or a scripted setup, there is a no-GUI
installer that runs GlanceCam as background Scheduled Tasks instead of a tray
app. Open **Windows PowerShell as administrator** (right-click it and choose
"Run as administrator") and run:

```powershell
irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 | iex
```

It sets up the same private Python runtime, app, and go2rtc engine under
`C:\GlanceCam` and serves the grid on port 9292, but with no tray icon. Use this
or the Setup.exe, not both: they bind the same ports, so uninstall one before
installing the other.

### Prefer Docker Desktop

If you already run Docker Desktop, the Docker quickstart above works on Windows
too: download `docker-compose.prod.yml`, then `docker compose up -d`.

### Just want a viewer on this PC

You do not have to install anything to *watch* the cameras. If GlanceCam is
already running somewhere on your network, any desktop is a browser client:
open `http://YOUR-HOST:9292` in Chrome, Edge, or another Chromium-based browser
and you have the grid. This works for any PC on your LAN, not just Windows.

To turn that browser into a dedicated full-screen display, make a shortcut that
launches it in app mode pointed at the server, for example:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --app=http://YOUR-HOST:9292/?kiosk=1
```

Press Alt+F4 to close it. The kiosk auto-behaviors described above (hiding
the settings gear, blocking navigation away from the grid) are specific to
the Raspberry Pi install; a remote browser like this one always gets the
plain camera grid, which is what you want on a shared desktop anyway.

## Updating

**Docker:** GlanceCam ships with Watchtower turned on by default, so a
running install picks up new releases on its own once a day. To update right
away instead of waiting, or if you turned Watchtower off:

```bash
cd /opt/glancecam   # or wherever you put docker-compose.yml
docker compose pull && docker compose up -d
```

To stop automatic updates, stop the Watchtower service:

```bash
docker compose stop watchtower
```

**Raspberry Pi native install:** re-run the exact same one-liner you used to
install it. It re-fetches the app, updates dependencies, and restarts the
services; your cameras and settings (under `/opt/glancecam/data`) are never
touched:

```bash
curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/install.sh | sudo bash
```

**Windows (Setup.exe):** download the newer GlanceCam-Setup.exe from the
[latest release](https://github.com/Syracuse3DPrintingOrg/GlanceCam/releases)
and run it. It installs over the existing version in place and keeps your
cameras and settings under `C:\ProgramData\GlanceCam`. The tray app does not
update itself in this release, so check the releases page when you want the
latest.

**Windows (headless PowerShell install):** re-run the same one-liner in an
administrator PowerShell. It refreshes the app, dependencies, and go2rtc, then
restarts the tasks; your cameras and settings (under `C:\GlanceCam\data`) are
never touched:

```powershell
irm https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/scripts/windows/install.ps1 | iex
```

## License

MIT. See [LICENSE](LICENSE).
