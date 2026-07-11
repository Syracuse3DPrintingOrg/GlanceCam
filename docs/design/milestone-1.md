# GlanceCam Milestone 1: Design Contract

This is the binding design for the first shippable release. Build agents:
follow the file map and interfaces here exactly so modules integrate without
rework. Reference implementation patterns live in the PantryRaider checkout at
`/home/dmarafino/code/PantryRaider` (cited below as `PR:<path>`).

## Product

GlanceCam is a minimalist, LAN-only IP camera viewer for quick glances. No
NVR, no recording, no cloud. One host serves two surfaces:

- **Web UI**: any browser on the LAN (including Windows PCs via Chromium).
- **Attached display**: a Raspberry Pi kiosk (Chromium in cage) rendering the
  same grid, gated so remote browsers never inherit panel settings.

Deployment targets: Docker on a server, native on a Raspberry Pi (with
optional kiosk), and any PC as a pure browser client.

## Stack decisions (locked)

- Python 3.11+ / FastAPI / Jinja2 / vanilla JS, Bootstrap 5 dark theme,
  minimalist. No frontend build step.
- **go2rtc** (bundled) is the only stream engine: RTSP/ONVIF/HTTP in,
  WebRTC/MSE/MJPEG out. The app owns go2rtc's stream table via its REST API.
  App never transcodes; go2rtc remuxes only.
- Storage: JSON files under `data/` (no database). `settings.json` for
  settings, `cameras.json` for the camera list, both written atomically.
- App port **9292**. go2rtc: API `1984`, RTSP `8554`, WebRTC `8555/tcp+udp`
  (bound to localhost in native mode; inter-container in Docker; WebRTC port
  must be exposed to the LAN for browsers).
- Internal identifier is `glancecam` everywhere (paths, units, env prefix
  `GLANCECAM_`). No legacy naming.
- Version: `APP_VERSION` in `service/app/config.py`, starts `0.1.0`.
  Keep-a-Changelog style `CHANGELOG.md`.

## Repository map (to be created)

```
service/
  Dockerfile
  requirements.txt
  app/
    main.py            FastAPI app, middleware, router includes
    config.py          pydantic Settings + hardened save/load
    templating.py      shared Jinja2 env + context (version, is_pi, kiosk flags)
    statefile.py       atomic mtime-gated JSON state file helper
    services/
      cameras.py       camera store (cameras.json) + go2rtc sync
      go2rtc.py        go2rtc REST client (streams CRUD, probe, health)
      netguard.py      SSRF guard (is_blocked_fetch_host)
      resources.py     hardware detection + stream budget
      discovery/
        __init__.py    aggregate discover() fan-out
        lanscan.py     TCP port + snapshot-path probe scan
        onvif.py       WS-Discovery multicast probe + profile/stream URI fetch
        reolink.py     Reolink HTTP API probe (channels, main/sub URLs)
        homeassistant.py  HA /api/states camera.* listing
    routers/
      ui.py            / (grid), /cam/{id} snapshot proxy, /display alias
      cameras.py       /api/cameras CRUD, reorder, test
      discovery.py     /api/discovery/* endpoints
      settings.py      /settings pages + /api/settings save + auth login/logout
      system.py        /api/system (resources, budget, health, version)
    templates/
      base.html  index.html  settings.html  login.html
    static/
      css/app.css
      js/grid.js  kiosk.js  settings.js  discovery.js
      vendor/video-rtc.js  video-stream.js   (go2rtc's WebRTC/MSE element, vendored)
tests/               pure-logic pytest, no network/Docker
docker/go2rtc/go2rtc.yaml   base go2rtc config
docker-compose.yml           dev (build)
docker-compose.prod.yml      pull GHCR + watchtower
install.sh                   Pi/Debian native installer (app+go2rtc+kiosk units)
scripts/pi/                  systemd units, kiosk setup, rotation helper
.github/workflows/publish-image.yml   GHCR multi-arch, image name PINNED
AGENTS.md  CLAUDE.md  README.md  CHANGELOG.md  LICENSE (MIT)
```

## Data model

`cameras.json`: `{"cameras": [Camera, ...]}` where Camera is:

```json
{
  "id": "cam_a1b2c3",            // stable slug, generated once
  "name": "Front Door",
  "enabled": true,
  "order": 0,
  "source": "manual|reolink|onvif|homeassistant",
  "main_url": "rtsp://...",       // main channel (required)
  "sub_url": "rtsp://... | null", // sub channel (auto-filled when possible)
  "snapshot_url": "http://... | null",
  "username": "", "password": "",  // stored server-side only, never sent to UI
  "ha_entity": null,               // homeassistant source only
  "main_resolution": [2560, 1440], // probed via go2rtc, null until known
  "sub_resolution": [640, 360],
  "fullscreen_uses_main": true     // per-camera override of the global default
}
```

Camera store API (services/cameras.py): `list_cameras()`, `get(id)`,
`add(dict) -> Camera`, `update(id, dict)`, `remove(id)`, `reorder([ids])`,
all persisted through `statefile.py`. Passwords never leave the server:
API responses replace them with `"__set__"` sentinel; an update with the
sentinel keeps the stored value (same pattern as PR restore secrets).

### go2rtc sync

`services/go2rtc.py` mirrors the camera store into go2rtc stream names
`{id}_main` and `{id}_sub` via `PUT/DELETE /api/streams`. Credentials are
embedded in the RTSP URL given to go2rtc only (server-to-server, never to
browsers). `probe(stream)` calls `GET /api/streams?src=` to read codec and
resolution and backfills `main_resolution`/`sub_resolution`. Browser playback
uses go2rtc's `video-stream` custom element (vendored JS) pointed at
`/go2rtc/api/ws?src={id}_sub` through an app-side reverse proxy route
(`/go2rtc/*` -> localhost:1984) so only one origin/port faces the LAN and the
settings password can gate it later.

## Streaming and layout rules

- Grid tiles always play the **sub stream** when one exists; main is for
  fullscreen. If no sub stream, the tile uses main but counts more against
  the budget (see resources).
- Tiles are laid out by `grid.js`: compute rows/cols from camera count and
  each stream's aspect ratio so tiles fill the viewport with minimal
  letterboxing (target: uniform grid, largest tile size that fits all
  visible cameras; landscape 16:9 default when resolution unknown).
- **Click to fullscreen**: one click/tap expands the tile to fill the screen
  and (by default, configurable globally and per-camera) swaps to the main
  channel. A second click returns to the grid and the sub stream. No other
  chrome on the grid; a small hover/idle-revealed gear links to /settings.
- Cameras beyond the live-tile budget render as **paused tiles**: a
  snapshot (refreshed ~10s) with a "tap to go live" badge; tapping swaps
  which tiles hold live slots.

## Resource gating (services/resources.py)

- Detect: Pi model (`/proc/device-tree/model`), CPU count, total RAM,
  rough class -> budget of concurrent decoded streams by pixel rate:
  a Pi 3 ~ 2 HD tiles, Pi 4 ~ 4-6 sub streams / 1-2 main, Pi 5 and x86 higher;
  a remote desktop browser gets a generous default (it decodes client-side,
  so the budget mostly protects go2rtc fan-out and LAN bandwidth).
- Expose `GET /api/system/budget` -> `{live_tile_limit, reasons: [...],
  recommendations: [...]}`. Recommendations are user-forward strings
  ("Front Door has no sub stream; fullscreen quality is unaffected but it
  uses a full HD slot in the grid").
- The web UI and settings page show the budget: "6 cameras, 4 live tiles on
  this display" and per-camera badges (live / snapshot-only / no sub stream).
- Budget is computed per surface: the kiosk (loopback) uses the host
  hardware class; remote browsers report `navigator.hardwareConcurrency`
  and viewport via a query param to `/api/system/budget`.

## Settings and auth

- Settings page sections: Cameras (add/edit/reorder/remove + discovery),
  Display (theme, rotation for kiosk, fullscreen-uses-main default,
  snapshot refresh), Access (optional settings password), System (version,
  update mode, diagnostics download, budget readout).
- **Optional settings password**: when set (hashed at rest), `/settings` and
  all mutating `/api/*` routes require a session login; the grid and streams
  stay open (the device itself is never locked, per requirements). Loopback
  is always trusted (kiosk). Constant-time compares. Pattern:
  PR:`service/app/main.py` `require_auth` middleware, simplified.
- `_SAVEABLE` allowlist + hardened save: corrupt-aside, `.bak` rollback,
  atomic write, chmod 600, hash password fields. Pattern:
  PR:`service/app/config.py` `save()`.

## Discovery (services/discovery/)

All discovery endpoints stream progress as JSON (simple polling job model:
`POST /api/discovery/scan` starts, `GET /api/discovery/scan/{job}` polls;
jobs in a module-level dict, one at a time).

1. **LAN scan** (`lanscan.py`): port-probe the /24 (prefer real LAN CIDR
   over docker bridge), ports `554, 8554, 80, 88, 443, 8000, 8080, 8443,
   37777`, then snapshot-path probing with brand tagging and image magic
   validation. Copy PR:`service/app/services/camera_scan.py` heavily.
2. **ONVIF** (`onvif.py`): WS-Discovery multicast (239.255.255.250:3702)
   Probe for NetworkVideoTransmitter, parse XAddrs; with credentials do
   GetProfiles/GetStreamUri over plain HTTP SOAP (no onvif lib dependency,
   hand-rolled minimal SOAP like go2rtc does) to auto-fill main+sub RTSP.
3. **Reolink** (`reolink.py`): given host+creds, token login
   (`cmd=Login`), `GetDevInfo`/`GetAbility`, enumerate channels, build
   `rtsp://host:554/h264Preview_{ch}_main|sub` plus snapshot CGI. Copy the
   token lease/retry from PR:`service/app/services/cameras.py` but keep
   creds out of URLs where the API allows.
4. **Home Assistant** (`homeassistant.py`): base URL + long-lived token ->
   `GET /api/states`, filter `camera.*`; feeds are proxied server-side with
   the bearer header (browser cannot). HA cameras are snapshot/MJPEG tiles
   (no sub/main split); note that in the UI.

Every discovery result returns proposed Camera dicts the UI can one-click
add (with a preview via `POST /api/cameras/test` which probes without
saving; SSRF-guarded fail-closed).

- SSRF guard (`netguard.py`): copy PR:`service/app/services/cameras.py`
  `is_blocked_fetch_host` semantics: resolve all IPs, block loopback,
  link-local, reserved, multicast; allow RFC1918; fail-closed for arbitrary
  test URLs, fail-open for saved cameras. Always `verify=True` unless the
  user explicitly toggles per-camera `allow_self_signed`.

## Kiosk (Pi attached display)

- `install.sh` native mode installs: python venv under `/opt/glancecam`,
  go2rtc binary (arch-detected from GitHub releases, pinned version) with
  systemd unit `glancecam-go2rtc.service`, app unit `glancecam.service`,
  and optional kiosk `glancecam-kiosk.service` (cage + chromium at
  `http://localhost:9292/?kiosk=1`) only when a display is present
  (PR:`scripts/image-build/firstboot.sh` `has_display()` logic, simplified).
- Rotation: compositor-only (wlr-randr via ExecStartPost), value from
  settings; expose in Display settings. No CSS rotation mode.
- **Loopback gating**: kiosk behaviors (auto-fullscreen grid, cursor hide,
  rotation, no settings gear) apply only when `location.hostname` is
  loopback AND server flag `is_pi`. Pattern: PR:`static/js/kiosk-display.js`.
- Touch: rely on cage/libinput defaults in M1; calibration is M2.

## Deploy

- `docker-compose.yml` (dev): build `./service`, volume-mount app with
  `--reload`, go2rtc official image `alexxit/go2rtc` with
  `docker/go2rtc/go2rtc.yaml` mounted, app talks to `go2rtc:1984`.
  WebRTC 8555 published to host.
- `docker-compose.prod.yml`: `ghcr.io/syracuse3dprintingorg/glancecam:latest`
  + go2rtc + Watchtower 1.7.1 label-scoped exactly like
  PR:`docker-compose.prod.yml` (label only on the app container).
- `publish-image.yml`: linux/amd64+arm64 buildx to GHCR on push to main and
  tags. Image name is the **literal string**
  `ghcr.io/syracuse3dprintingorg/glancecam`, never derived from
  `github.repository` (see PR workflow comment for why).
- Windows: documented as Chromium/Edge pointed at the server URL; optional
  `--kiosk` shortcut snippet in README. No native Windows service in M1.

## Testing (Definition of Done)

`python -m pytest tests/ -q` green and the import smoke test passes:
`python -c "import sys; sys.path.insert(0,'service'); from app.main import app"`.
Tests are pure logic: camera store round-trip, settings save hardening,
SSRF guard cases, budget math, discovery parsers (ONVIF XML, Reolink JSON,
scan path tagging) against fixture payloads, layout math in grid.js is
mirrored in a tiny pure function tested via its Python twin in resources.

## Milestone 2 (file beads, do not build)

Stream Deck camera engine (thumbnail keys ~1s refresh, press = fullscreen),
HA event-driven camera pop-ups, remote access (phase 2), touch calibration,
mDNS hostname (`glance.local`), snapshot-only export, audio.
