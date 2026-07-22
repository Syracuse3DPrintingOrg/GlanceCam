# GlanceCam Stream Deck controller

Turn an Elgato Stream Deck into a camera wall for GlanceCam. Each key shows a
live thumbnail of one camera, refreshing about once a second, and a press picks
that camera. It runs as a small background service next to (or pointed at) your
GlanceCam app and talks only to the app over HTTP, so the camera passwords never
leave the server.

## What it shows and does

- **Live thumbnails.** The controller reads your cameras from the app
  (`GET /api/cameras`) and pulls each one's still from the server-side snapshot
  proxy (`GET /cam/{id}/snapshot`), which fetches the frame with the stored
  credentials and sizes it to the camera. Every key updates on its own about once
  a second (see `poll_seconds`); a still scene skips the redundant USB write.
- **A press opens the camera.** Pressing a key brings that camera up full screen
  on the attached display (see "What a press does").
- **More cameras than keys.** When you have more cameras than the deck has keys,
  the last key becomes a **More** key that pages through the rest.
- **Survives unplugging.** Losing, unplugging, or replugging the deck is
  recovered in place with a backoff, and saving a new layout is picked up without
  a restart.

## How cameras map to keys

By default the keys auto-fill from your camera list in the same order the grid
uses, left to right and top to bottom. To pin cameras to specific keys, set the
`keys` list in the config to the camera ids in the order you want, using an empty
string for a blank key:

```toml
keys = ["cam_1a2b3c", "cam_4d5e6f", "", "cam_7g8h9i"]
```

This is the same per-key assignment the drag-and-drop editor writes, so a layout
built in the browser and a hand-edited config are the same shape. Leave `keys`
empty for the automatic layout.

Rotation (`rotation = 0 | 90 | 180 | 270`) turns the rendered faces and remaps
presses to match, so a deck mounted on its side or upside down still lines up.

## Install

The controller is a standard Python package run as `python -m glancecam_streamdeck`.
`install.sh` sets it up on a device (creating the service user, the virtualenv,
the config at `/etc/glancecam/streamdeck.toml`, and the systemd + udev units);
wiring that into the installer is handled elsewhere. The pieces here are:

- `glancecam_streamdeck/` — the package.
- `systemd/glancecam-streamdeck.service` — a unit template. The installer fills
  in `__DECK_USER__`, `__DECK_UID__`, `__VENV__`, and `__WORKDIR__`, and points
  `GLANCECAM_STREAMDECK_CONFIG` at `/etc/glancecam/streamdeck.toml`.
- `udev/99-glancecam-streamdeck.rules` — grants the controller access to the
  deck without root (Elgato vendor `0fd9`, `uaccess` tag plus the `plugdev`
  group).
- `config.example.toml` — documented defaults (`base_url` is
  `http://127.0.0.1:9292`).
- `requirements.txt` — `streamdeck`, `Pillow`, `httpx`.

To try it by hand:

```bash
pip install -r streamdeck/requirements.txt
GLANCECAM_STREAMDECK_CONFIG=streamdeck/config.example.toml \
  python -m glancecam_streamdeck --verbose
```

## What a press does

Pressing a key opens that camera full screen on the attached display. The
controller posts the choice to the app's on-screen event channel
(`POST /events/camera-popup`, the same channel a Home Assistant automation uses
to pop a camera up), and the kiosk grid follows it. The display holds the camera
for `popup_seconds` (default 30, `0` keeps it up until the next press or a viewer
tap). The press is also recorded to `<data_dir>/deck-selection.json` (an atomic
JSON write) as a durable record. The event endpoint only trusts callers on the
local network, which the deck always is.
