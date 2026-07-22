# Changelog

All notable changes to GlanceCam are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). GlanceCam is
pre-1.0, so it stays in the `0.x` range until the first public release.

## [Unreleased]

## [0.2.0] - 2026-07-16

### Added

- **Control the wall with a Stream Deck.** Plug an Elgato Stream Deck into a
  Raspberry Pi and each key becomes a live camera thumbnail, refreshing about
  once a second. Press a key and that camera fills the screen; press another to
  switch. More cameras than keys paginate with a "More" key. The Pi installer
  sets it up automatically when a deck is plugged in.
- **Cameras that pop up when something happens.** A Home Assistant automation
  (or anything on your network) can tell GlanceCam to bring a camera full
  screen for a few seconds, so a person at the door or motion in the yard puts
  the right camera on the wall on its own. The display returns to the grid
  afterward.
- **Sound in full screen.** When a camera is full screen, a speaker button in
  the corner unmutes its audio. The grid itself stays silent, and full screen
  starts muted until you ask for sound.
- **A friendly name on the network.** A new Raspberry Pi install reaches the
  web page at `http://glance.local:9292`, and if you set up more than one, each
  takes the next free name (`glance-2`, `glance-3`, and so on) so they never
  collide.
- **Touchscreens land where you tap.** The Pi installer now calibrates a
  connected touchscreen to the display's rotation, so taps line up with the
  tiles on a rotated panel.

## [0.1.0] - 2026-07-16

### Added

- **Run GlanceCam on a Windows PC.** Download `GlanceCam-Setup.exe` from the
  latest release and run it: GlanceCam installs like any other Windows app,
  with a Start Menu entry, a system tray icon to open, stop, or start it (and
  a "start when I sign in" toggle), and a normal uninstall in Programs and
  Features. No Docker, Raspberry Pi, or server required, and your cameras and
  settings survive updates and uninstalls. A scripted PowerShell install
  remains for headless machines.
- **An on-screen menu on the grid.** Tap the top-left corner of the screen
  (or press m on a keyboard) for a translucent bar with the time, the date,
  and one-tap switching between your saved grid layouts. It tucks itself away
  after a few seconds.
- **Cameras that only speak H265 always show a picture.** If a browser or
  display cannot play a camera's full-quality H265 stream, full screen now
  stays on the stream it can play and says so once, instead of going black.
- **Works without internet.** The web interface no longer loads anything from
  the internet, so a camera wall on an isolated network renders correctly.
- **Grid layouts you design.** A new Grid section in settings lets you arrange
  tiles by hand: drag a camera onto the grid, drag tiles to move them, and
  drag a tile's corner to make it any size, on a mouse or the touchscreen.
  Save as many named layouts as you like and switch the display between them.
  Automatic stays the default and now sizes and places tiles by itself, giving
  ultrawide cameras (like the Reolink Duo) double width so they are not
  squeezed into a single square. Editing a layout updates any open grid right
  away, including the device's own screen.
- **First look at GlanceCam.** A minimalist, LAN-only camera viewer that
  shows all your IP cameras in one grid you can glance at. Add a camera by its
  RTSP or snapshot address, and it appears live on the grid. Click any tile to
  fill the screen with that camera (switching to its full-quality main stream
  by default), click again to return. No recording, no cloud, no accounts.
- **Bundled streaming.** GlanceCam runs go2rtc for you and manages its streams
  automatically, so cameras play in the browser over WebRTC with a snapshot
  fallback when a stream is still warming up. Each camera can carry a main
  and a sub channel, and the grid always plays the lighter sub stream.
- **Find cameras automatically.** Enter your camera login once (or pick a
  saved one), press "Find my cameras", and GlanceCam sweeps your network,
  works out each camera's main and sub streams by itself, and shows a
  thumbnail with a single "Add to grid" button. Cameras already on the grid
  say so instead of offering themselves twice. ONVIF search, Reolink channel
  listing, and Home Assistant cameras sit under "More ways to find cameras",
  and every result can still be previewed and picked by hand.
- **Fits your hardware.** GlanceCam measures what the device showing the grid
  can smoothly decode (a Raspberry Pi 4 handles a few live streams, a desktop
  browser many more) and keeps that many tiles live; the rest show refreshing
  snapshots you can tap to bring live. The System section explains the limit
  and suggests improvements, like adding a sub channel to a camera that lacks
  one.
- **Optional settings password.** Lock the settings and camera list behind a
  password while leaving the live grid open, so the wall display is never in
  your way but changes stay protected. The device's own screen is always
  trusted, and the password can be removed again from the Access section.
- **Install on a server or a Raspberry Pi.** A published container image on
  GHCR plus a one-line installer
  (`curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/install.sh | sudo bash`)
  that sets up either a Docker Compose stack or a native Raspberry Pi install
  with its own go2rtc engine. On a Pi with a display attached, it can also
  turn the screen into a dedicated full-screen camera kiosk with rotation
  support. Re-running the same command later updates the install in place.
