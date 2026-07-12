# Changelog

All notable changes to GlanceCam are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). GlanceCam is
pre-1.0, so it stays in the `0.x` range until the first public release.

## [Unreleased]

### Added

- **Run GlanceCam on a Windows PC.** A one-line installer sets up GlanceCam
  natively on Windows 10 or 11, with no Docker, Raspberry Pi, or server
  required. Run it in an administrator PowerShell and it installs a private
  Python runtime, the app, and the bundled go2rtc engine under `C:\GlanceCam`,
  registers them as startup tasks so the grid comes back with the PC, and
  opens the firewall for you. Open `http://localhost:9292` when it finishes.
  Re-running the same command updates the install in place and leaves your
  cameras and settings alone.
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
