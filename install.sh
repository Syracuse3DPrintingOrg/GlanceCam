#!/usr/bin/env bash
# GlanceCam installer
# ====================
# The one-line install (Raspberry Pi or any Debian/Ubuntu box), read it
# before piping it into a shell:
#
#   curl -fsSL https://raw.githubusercontent.com/Syracuse3DPrintingOrg/GlanceCam/main/install.sh | sudo bash
#
# Re-running the same command later is the update path: it re-fetches the
# app, reinstalls dependencies, and restarts the services in place. Your
# cameras and settings (under /opt/glancecam/data) are never touched.
#
# This script never assumes it is running from a checkout: piped through
# `curl | bash`, it has no file of its own on disk, so it fetches the repo
# itself (git clone, or a tarball download if git is unavailable).
#
# Two install modes:
#   native  - installs Python, the app, and go2rtc directly on the host, with
#             systemd units. The default on a Raspberry Pi. If a display is
#             attached, it also offers a Chromium kiosk that shows the camera
#             grid full-screen.
#   docker  - runs the published image plus go2rtc under Docker Compose. The
#             default on anything that is not a Raspberry Pi.
#
# Everything this script would otherwise prompt for has an environment
# variable and a flag, and it NEVER prompts when stdin is not a terminal
# (which is always true for `curl | bash`) -- it just uses the default:
#
#   GLANCECAM_MODE       native | docker      (default: see above)
#   GLANCECAM_KIOSK       auto | true | false  (default: auto -- installs the
#                          kiosk only if a display is attached; native mode only)
#   GLANCECAM_ROTATION    normal | 90 | 180 | 270   (default: normal)
#   GLANCECAM_HOSTNAME    <name>               (default: glance -- on a Pi the box
#                          answers at <name>.local; a -2/-3 suffix is added when
#                          the name is already taken on the LAN. Native Pi only.)
#   GLANCECAM_TOUCH       auto | ads7846 | usb | none  (default: auto -- kiosk
#                          touchscreen calibration; native kiosk only)
#   GLANCECAM_TOUCH_MATRIX "a b c d e f"       (override the libinput calibration
#                          matrix; default: derived from GLANCECAM_ROTATION)
#   GLANCECAM_STREAMDECK  auto | true | false  (default: auto -- installs the
#                          Stream Deck controller only when an Elgato deck is
#                          attached; native mode only)
#
# Flags mirror the first three settings: --mode, --kiosk, --rotation, plus
# --yes (skip the one interactive prompt this script ever asks, redundant
# when piped since piping already implies non-interactive).
set -euo pipefail

REPO_OWNER="${REPO_OWNER:-Syracuse3DPrintingOrg}"
REPO_NAME="${REPO_NAME:-GlanceCam}"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_URL="${REPO_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}.git}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}}"
TARBALL_URL="${TARBALL_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${REPO_BRANCH}.tar.gz}"

INSTALL_DIR="${INSTALL_DIR:-/opt/glancecam}"
# Native-mode checkout: this IS the running app tree (glancecam.service's
# WorkingDirectory is $REPO_DIR/service), so re-fetching here and restarting
# the service is the entire update path. Never holds camera data or settings;
# those live under $INSTALL_DIR/data via GLANCECAM_DATA_DIR, outside the
# checkout, so a git reset --hard on update can never touch them.
REPO_DIR="${REPO_DIR:-$INSTALL_DIR/app}"

# Pinned deliberately: go2rtc renames or reshapes its release assets between
# major versions, and an unpinned "latest" would break the arch-detection
# mapping below without warning on some future release. Bump this string on
# purpose (and re-check the asset names it downloads) rather than tracking
# latest automatically.
GO2RTC_VERSION="${GO2RTC_VERSION:-v1.9.14}"

# -- pretty output --------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_CYAN=$'\033[1;36m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'
  C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_OFF=""
fi
say()  { printf '%s==>%s %s\n' "$C_CYAN" "$C_OFF" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s[!]%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; }
die()  { printf '%sError:%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }
hr()   { printf '%s----------------------------------------%s\n' "$C_DIM" "$C_OFF"; }

# Prompting is only ever attempted when stdin is a real terminal. `curl |
# sudo bash` always fails this (stdin is the pipe), so a piped install never
# blocks waiting for input; it falls straight through to the documented
# defaults and env vars above.
can_prompt() { [ -t 0 ]; }

prompt_yn() {  # prompt default(y|n) -> returns 0 for yes, 1 for no
  local prompt="$1" def="$2" hint ans
  case "$def" in y|Y) hint="[Y/n]";; *) hint="[y/N]";; esac
  while :; do
    printf '%s%s %s%s ' "$C_CYAN" "$prompt" "$hint" "$C_OFF"
    IFS= read -r ans || ans=""
    ans="${ans:-$def}"
    case "$ans" in
      y|Y|yes|YES) return 0 ;;
      n|N|no|NO)   return 1 ;;
      *) printf '  Please answer y or n.\n' ;;
    esac
  done
}

# -- hardware detection ----------------------------------------------------
is_raspberry_pi() {
  [ -n "${FORCE_PI:-}" ] && return 0
  local f
  for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
    [ -r "$f" ] && tr -d '\0' <"$f" | grep -qi 'raspberry pi' && return 0
  done
  return 1
}

# An existing X/Wayland session or a connected DRM connector counts; a bare
# /dev/dri/card0 does NOT (the vc4 KMS driver creates that node even on a
# headless Pi, which would otherwise offer the kiosk everywhere).
has_display() {
  case "${FORCE_DISPLAY:-}" in
    0|false|FALSE|no) return 1 ;;
    ?*) return 0 ;;
  esac
  [ -n "${WAYLAND_DISPLAY:-}" ] && return 0
  [ -n "${DISPLAY:-}" ] && return 0
  local st
  for st in "${DRM_SYS_ROOT:-/sys/class/drm}"/*/status; do
    [ -r "$st" ] && grep -qx connected "$st" 2>/dev/null && return 0
  done
  return 1
}

board_model() {
  local f
  for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
    [ -r "$f" ] && { tr -d '\0' <"$f"; return; }
  done
  echo "unknown"
}

# uid 1000 is the interactive user on a standard Raspberry Pi OS image
# (created by the imager). Falls back to empty when there is none, in which
# case the kiosk installer creates a dedicated "glancecam-kiosk" user (Pi OS
# Lite run headless-then-attached, or a display plugged into a server later).
primary_user() {
  getent passwd 1000 2>/dev/null | cut -d: -f1
}

# -- args -------------------------------------------------------------------
GLANCECAM_MODE="${GLANCECAM_MODE:-}"
GLANCECAM_KIOSK="${GLANCECAM_KIOSK:-auto}"
GLANCECAM_ROTATION="${GLANCECAM_ROTATION:-normal}"
# Hostname: empty means "use the brand default (glance) but only take over a
# stock/unset name". A non-empty value is an explicit request that always wins
# (with collision suffixing), so remember whether one was given.
GLANCECAM_HOSTNAME="${GLANCECAM_HOSTNAME:-}"
HOSTNAME_EXPLICIT=0; [ -n "$GLANCECAM_HOSTNAME" ] && HOSTNAME_EXPLICIT=1
GLANCECAM_TOUCH="${GLANCECAM_TOUCH:-auto}"
GLANCECAM_TOUCH_MATRIX="${GLANCECAM_TOUCH_MATRIX:-}"
GLANCECAM_STREAMDECK="${GLANCECAM_STREAMDECK:-auto}"
ASSUME_YES=0

usage() {
  cat <<EOF
Usage: curl -fsSL .../install.sh | sudo bash
   or: sudo bash install.sh [--mode native|docker] [--kiosk auto|true|false] [--rotation normal|90|180|270] [--yes]

  --mode MODE       native or docker. Default: native on a Raspberry Pi,
                     docker everywhere else. Env: GLANCECAM_MODE
  --kiosk VALUE      auto, true, or false (native mode only). auto installs
                     the kiosk only when a display is attached. Env: GLANCECAM_KIOSK
  --rotation VALUE   Kiosk display rotation (native mode only): normal, 90,
                     180, or 270. Env: GLANCECAM_ROTATION
  --yes              Skip the confirmation prompt (implied when piped).
  -h, --help         Show this help.

Re-running this script (the exact same one-liner) updates an existing
install in place.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) GLANCECAM_MODE="$2"; shift 2 ;;
    --mode=*) GLANCECAM_MODE="${1#*=}"; shift ;;
    --kiosk) GLANCECAM_KIOSK="$2"; shift 2 ;;
    --kiosk=*) GLANCECAM_KIOSK="${1#*=}"; shift ;;
    --rotation) GLANCECAM_ROTATION="$2"; shift 2 ;;
    --rotation=*) GLANCECAM_ROTATION="${1#*=}"; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done

case "$GLANCECAM_ROTATION" in
  normal|0|90|180|270) : ;;
  *) die "--rotation must be one of: normal, 90, 180, 270" ;;
esac
[ "$GLANCECAM_ROTATION" = "0" ] && GLANCECAM_ROTATION="normal"

case "$GLANCECAM_KIOSK" in
  auto|true|false) : ;;
  *) die "--kiosk must be one of: auto, true, false" ;;
esac

case "$GLANCECAM_TOUCH" in
  auto|ads7846|usb|none) : ;;
  *) die "GLANCECAM_TOUCH must be one of: auto, ads7846, usb, none" ;;
esac

case "$GLANCECAM_STREAMDECK" in
  auto|true|false) : ;;
  *) die "GLANCECAM_STREAMDECK must be one of: auto, true, false" ;;
esac

# -- root ---------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "This installer needs root. Re-run with sudo."

# -- plan -----------------------------------------------------------------
IS_PI=false; is_raspberry_pi && IS_PI=true
HAS_DISPLAY=false; has_display && HAS_DISPLAY=true

if [ -z "$GLANCECAM_MODE" ]; then
  if [ "$IS_PI" = true ]; then GLANCECAM_MODE="native"; else GLANCECAM_MODE="docker"; fi
fi
case "$GLANCECAM_MODE" in
  docker|native) : ;;
  *) die "--mode must be docker or native (got: $GLANCECAM_MODE)" ;;
esac

ENABLE_KIOSK=false
if [ "$GLANCECAM_MODE" = "native" ]; then
  case "$GLANCECAM_KIOSK" in
    true) ENABLE_KIOSK=true ;;
    false) ENABLE_KIOSK=false ;;
    auto)
      if [ "$HAS_DISPLAY" = true ] && can_prompt && [ "$ASSUME_YES" -ne 1 ]; then
        prompt_yn "Display detected. Install the full-screen camera kiosk?" y \
          && ENABLE_KIOSK=true || ENABLE_KIOSK=false
      else
        # Piped (no tty) or --yes: never prompt, just follow the hardware.
        ENABLE_KIOSK="$([ "$HAS_DISPLAY" = true ] && echo true || echo false)"
      fi
      ;;
  esac
fi

banner() {
  hr
  printf '%s  GlanceCam installer%s\n' "$C_GREEN" "$C_OFF"
  hr
  if [ "$IS_PI" = true ]; then say "Device: $(board_model)"; else say "Device: non-Pi host ($(uname -m))"; fi
  say "Mode: $GLANCECAM_MODE"
  if [ "$GLANCECAM_MODE" = "native" ]; then
    say "Display attached: $([ "$HAS_DISPLAY" = true ] && echo yes || echo no)"
    say "Kiosk: $ENABLE_KIOSK$([ "$GLANCECAM_ROTATION" != normal ] && printf ' (rotated %s)' "$GLANCECAM_ROTATION")"
  fi
  hr
}
banner

# -- fetch the app tree --------------------------------------------------
# Always fetches on every run (this IS the update path); never reads from a
# local checkout or relies on this script's own location on disk, so it
# behaves identically whether it was saved to a file or piped straight into
# bash.
fetch_via_git() {
  command -v git >/dev/null 2>&1 || return 1
  if [ -d "$REPO_DIR/.git" ]; then
    say "Updating existing checkout at $REPO_DIR"
    git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH" || return 1
    git -C "$REPO_DIR" reset --hard "origin/$REPO_BRANCH" || return 1
  else
    say "Cloning GlanceCam to $REPO_DIR"
    rm -rf "$REPO_DIR"
    git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR" || return 1
  fi
  return 0
}

fetch_via_tarball() {
  say "git unavailable; downloading a tarball of $REPO_BRANCH instead"
  command -v curl >/dev/null 2>&1 || die "Neither git nor curl is available; cannot fetch GlanceCam."
  rm -rf "$REPO_DIR"
  mkdir -p "$REPO_DIR"
  curl -fsSL "$TARBALL_URL" | tar -xz --strip-components=1 -C "$REPO_DIR" \
    || die "Could not download or extract $TARBALL_URL"
}

fetch_repo() {
  mkdir -p "$INSTALL_DIR"
  # A pre-populated checkout (the CI smoke test copies the repo into place and
  # sets this) skips the network fetch entirely, so the installer runs against
  # exactly the code under test rather than a branch that only exists locally.
  if [ -n "${GLANCECAM_SKIP_FETCH:-}" ] && [ -e "$REPO_DIR/service/app/main.py" ]; then
    ok "Using the checkout already at $REPO_DIR (fetch skipped)"
    return 0
  fi
  if fetch_via_git; then
    ok "Repo ready at $REPO_DIR"
    return 0
  fi
  warn "git clone/update failed or git is not installed; trying apt then a tarball fallback"
  if ! command -v git >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 && apt-get install -y --no-install-recommends git ca-certificates >/dev/null 2>&1 || true
  fi
  if command -v git >/dev/null 2>&1 && fetch_via_git; then
    ok "Repo ready at $REPO_DIR"
    return 0
  fi
  fetch_via_tarball
  ok "Repo ready at $REPO_DIR (tarball)"
}

# ==============================================================================
# Docker mode
# ==============================================================================
ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker and Compose already installed"
    return 0
  fi
  say "Installing Docker"
  if curl -fsSL https://get.docker.com | sh; then
    :
  else
    warn "get.docker.com script failed; falling back to distro packages"
    apt-get update -y
    apt-get install -y --no-install-recommends docker.io docker-compose-plugin \
      || die "Could not install Docker. Install it manually and re-run."
  fi
  systemctl enable --now docker 2>/dev/null || true
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
    || die "Docker installed but 'docker compose' is not available."
}

install_docker_mode() {
  ensure_docker
  mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/docker/go2rtc"

  say "Fetching docker-compose.prod.yml and go2rtc.yaml"
  curl -fsSL "$RAW_BASE/docker-compose.prod.yml" -o "$INSTALL_DIR/docker-compose.yml" \
    || die "Could not download the compose file from $RAW_BASE"
  curl -fsSL "$RAW_BASE/docker/go2rtc/go2rtc.yaml" -o "$INSTALL_DIR/docker/go2rtc/go2rtc.yaml" \
    || die "Could not download docker/go2rtc/go2rtc.yaml from $RAW_BASE"

  say "Starting GlanceCam"
  ( cd "$INSTALL_DIR" && docker compose up -d ) || die "docker compose up failed"
  ok "GlanceCam is running under Docker"
}

# ==============================================================================
# Native mode
# ==============================================================================
GLANCECAM_USER="glancecam"

ensure_native_deps() {
  say "Installing system packages (Python, venv, curl, ffmpeg)"
  apt-get update -y
  # ffmpeg lets go2rtc make JPEG previews and snapshots from H265 cameras.
  apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip curl ca-certificates git ffmpeg \
    || die "Package install failed"
}

ensure_glancecam_user() {
  if id -u "$GLANCECAM_USER" >/dev/null 2>&1; then
    return 0
  fi
  say "Creating system user '$GLANCECAM_USER'"
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin \
    --create-home "$GLANCECAM_USER" || die "Could not create the $GLANCECAM_USER user"
}

# Asset names as published for the pinned GO2RTC_VERSION (verified against
# github.com/AlexxIT/go2rtc/releases): go2rtc_linux_amd64, go2rtc_linux_arm64,
# go2rtc_linux_arm (32-bit hard-float, i.e. armv7), go2rtc_linux_armv6
# (older Pi Zero/1). A Raspberry Pi OS Lite 64-bit image (the primary target)
# reports aarch64 and gets go2rtc_linux_arm64. Re-check this mapping if
# GO2RTC_VERSION is bumped and the release renames its assets.
go2rtc_asset_name() {
  case "$(uname -m)" in
    x86_64|amd64) echo "go2rtc_linux_amd64" ;;
    aarch64|arm64) echo "go2rtc_linux_arm64" ;;
    armv7l) echo "go2rtc_linux_arm" ;;
    armv6l) echo "go2rtc_linux_armv6" ;;
    *) die "Unsupported architecture for go2rtc: $(uname -m)" ;;
  esac
}

install_go2rtc() {
  mkdir -p "$INSTALL_DIR/go2rtc"
  local asset url
  asset="$(go2rtc_asset_name)"
  url="https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${asset}"
  say "Downloading go2rtc $GO2RTC_VERSION ($asset)"
  curl -fsSL "$url" -o "$INSTALL_DIR/go2rtc/go2rtc.new" \
    || die "Could not download go2rtc from $url"
  chmod +x "$INSTALL_DIR/go2rtc/go2rtc.new"
  mv -f "$INSTALL_DIR/go2rtc/go2rtc.new" "$INSTALL_DIR/go2rtc/go2rtc"

  # Adapt the base config for native mode: the REST API stays on loopback
  # (nothing needs it from the LAN; the app reverse-proxies stream traffic),
  # WebRTC (8555) stays open on all interfaces so LAN browsers can connect.
  sed 's/listen: ":1984"/listen: "127.0.0.1:1984"/' "$REPO_DIR/docker/go2rtc/go2rtc.yaml" \
    > "$INSTALL_DIR/go2rtc/go2rtc.yaml"
}

install_app_tree() {
  say "Installing Python dependencies (this can take a minute)"
  mkdir -p "$INSTALL_DIR/data"
  if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
  fi
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
  "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/service/requirements.txt" \
    || die "pip install failed"
}

install_core_units() {
  say "Installing systemd units"
  cp -f "$REPO_DIR/scripts/pi/glancecam.service" /etc/systemd/system/glancecam.service
  cp -f "$REPO_DIR/scripts/pi/glancecam-go2rtc.service" /etc/systemd/system/glancecam-go2rtc.service
  # glancecam.service's WorkingDirectory points straight at the checkout
  # ($REPO_DIR/service), so ownership needs to cover the checkout too, not
  # just the venv/go2rtc/data directories.
  chown -R "$GLANCECAM_USER:$GLANCECAM_USER" "$INSTALL_DIR"
  # GLANCECAM_SKIP_SERVICES=1 skips every systemctl call below (used by the
  # CI smoke test, which installs into a plain container with no systemd as
  # PID 1; daemon-reload/enable would abort the script under set -e there).
  if [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ]; then
    warn "GLANCECAM_SKIP_SERVICES=1; leaving the units installed but not enabled/started"
    return 0
  fi
  systemctl daemon-reload
  systemctl enable glancecam-go2rtc.service glancecam.service
  # restart (not enable --now): on a re-run this is the update path, and
  # restart both starts a fresh install and picks up new code on an existing
  # one.
  systemctl restart glancecam-go2rtc.service || warn "glancecam-go2rtc restart failed"
  systemctl restart glancecam.service || warn "glancecam restart failed"
}

# Kiosk (opt-in, only called when ENABLE_KIOSK=true). Adapted from the
# Pantry Raider kiosk installer (scripts/image-build/firstboot.sh
# configure_kiosk), simplified: no accelerometer, no Stream Deck, no theming.
#
# Package names: on Raspberry Pi OS (Bookworm) the browser package is
# "chromium"; older Pi OS releases and some Debian derivatives use
# "chromium-browser". Both are tried. cage (the Wayland kiosk compositor)
# needs a real seat to get DRM/VT access when run as a non-logind service
# user, which is what seatd provides; it is installed and the kiosk user is
# added to its group below.
# A fully transparent XCursor theme, written byte for byte (xcursorgen is not
# always installable). "Xcur" magic, one TOC entry, one 1x1 image whose only
# pixel is ARGB 0x00000000. Cage has no hide-cursor flag and CSS cursor:none
# cannot hide the compositor's own seat cursor, so the theme is the reliable
# way to blank it on a touch appliance. Echoes the theme name on success.
_install_blank_cursor_theme() {
  local theme="glancecam-hidden"
  local cdir="/usr/share/icons/$theme/cursors"
  mkdir -p "$cdir" || return 1
  if ! printf '\130\143\165\162\020\000\000\000\000\000\001\000\001\000\000\000\002\000\375\377\001\000\000\000\034\000\000\000\044\000\000\000\002\000\375\377\001\000\000\000\001\000\000\000\001\000\000\000\001\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000' > "$cdir/left_ptr"; then
    return 1
  fi
  local name
  for name in default left_ptr_watch watch text xterm hand1 hand2 pointer \
              top_left_arrow arrow crosshair fleur grabbing; do
    ln -sf left_ptr "$cdir/$name" 2>/dev/null || true
  done
  cat > "/usr/share/icons/$theme/index.theme" <<'THEME'
[Icon Theme]
Name=GlanceCam Hidden Cursor
Comment=Fully transparent cursor for the touch kiosk
THEME
  echo "$theme"
}

# The vc4 HDMI CEC input devices advertise relative axes, which hands the
# Wayland seat a pointer capability and makes cage draw a cursor on a device
# with no mouse at all. The kiosk never uses CEC input; ignore those devices.
_install_cec_pointer_ignore_rule() {
  local rules="/etc/udev/rules.d/71-glancecam-cec-pointer.rules"
  [ -f "$rules" ] && return 0
  cat > "$rules" <<'RULES'
# GlanceCam kiosk: vc4 HDMI CEC input devices expose relative axes, which
# grows the Wayland seat a pointer and draws a cursor with no mouse attached.
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="vc4-hdmi*", ENV{LIBINPUT_IGNORE_DEVICE}="1"
RULES
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --subsystem-match=input 2>/dev/null || true
}

install_kiosk() {
  say "Installing the Chromium kiosk (cage + chromium)"
  apt-get install -y --no-install-recommends cage seatd wlr-randr || warn "kiosk package install had failures"
  apt-get install -y --no-install-recommends chromium || apt-get install -y --no-install-recommends chromium-browser \
    || { warn "chromium install failed; skipping kiosk"; return 0; }

  local cage_bin chromium_bin
  cage_bin="$(command -v cage || true)"
  chromium_bin="$(command -v chromium || command -v chromium-browser || true)"
  if [ -z "$cage_bin" ] || [ -z "$chromium_bin" ]; then
    warn "kiosk binaries missing (cage=${cage_bin:-none} chromium=${chromium_bin:-none}); skipping kiosk service"
    return 0
  fi

  local kuser kuid
  kuser="$(primary_user)"
  if [ -z "$kuser" ]; then
    kuser="glancecam-kiosk"
    if ! id -u "$kuser" >/dev/null 2>&1; then
      say "No interactive user found; creating '$kuser' for the kiosk session"
      useradd --create-home --shell /usr/sbin/nologin "$kuser" || { warn "could not create $kuser; skipping kiosk"; return 0; }
    fi
  fi
  kuid="$(id -u "$kuser")"

  loginctl enable-linger "$kuser" 2>/dev/null || true
  systemctl disable getty@tty1.service 2>/dev/null || true

  # Some distros ship seatd with a dedicated "_seatd"/"seatd" group; current
  # Raspberry Pi OS (trixie) ships none and gates the socket on "video"
  # instead. Grant whichever exists, plus video/render for direct DRM/GPU.
  if getent group _seatd >/dev/null 2>&1; then
    usermod -aG _seatd "$kuser" || warn "could not add $kuser to _seatd"
  elif getent group seatd >/dev/null 2>&1; then
    usermod -aG seatd "$kuser" || warn "could not add $kuser to seatd"
  fi
  for g in video render input; do
    getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$kuser" 2>/dev/null
  done
  systemctl enable --now seatd 2>/dev/null || warn "seatd enable/start failed"

  mkdir -p /etc/glancecam
  printf '%s\n' "$GLANCECAM_ROTATION" > /etc/glancecam/kiosk-rotation

  mkdir -p /opt/glancecam/bin
  cp -f "$REPO_DIR/scripts/pi/apply-rotation" /opt/glancecam/bin/apply-rotation
  chmod +x /opt/glancecam/bin/apply-rotation

  sed \
    -e "s#__KIOSK_USER__#$kuser#g" \
    -e "s#__KIOSK_UID__#$kuid#g" \
    -e "s#__KIOSK_URL__#http://localhost:9292/?kiosk=1#g" \
    -e "s#__CAGE_BIN__#$cage_bin#g" \
    -e "s#__CHROMIUM_BIN__#$chromium_bin#g" \
    "$REPO_DIR/scripts/pi/glancecam-kiosk.service" > /etc/systemd/system/glancecam-kiosk.service

  # Hide the pointer on the appliance screen (GLANCECAM_HIDE_CURSOR=false to
  # keep it, e.g. when a mouse is attached).
  if [ "${GLANCECAM_HIDE_CURSOR:-true}" != "false" ]; then
    local cursor_theme
    cursor_theme="$(_install_blank_cursor_theme || true)"
    if [ -n "$cursor_theme" ]; then
      sed -i "/^Environment=LIBSEAT_BACKEND=seatd/a Environment=XCURSOR_PATH=/usr/share/icons\nEnvironment=XCURSOR_THEME=$cursor_theme\nEnvironment=XCURSOR_SIZE=24" \
        /etc/systemd/system/glancecam-kiosk.service
    else
      warn "could not install the transparent cursor theme; the cursor stays visible"
    fi
    _install_cec_pointer_ignore_rule
  fi

  systemctl daemon-reload
  systemctl enable glancecam-kiosk.service || warn "kiosk enable failed"
  systemctl restart glancecam-kiosk.service || warn "kiosk (re)start failed (will retry on boot)"
  ok "Kiosk installed (rotation: $GLANCECAM_ROTATION)"
}

# ==============================================================================
# mDNS hostname (Pi native only): make the box answer at <name>.local, adding a
# -2/-3 suffix when the desired name is already taken on the LAN (GlanceCam-7c5).
# ==============================================================================

# Ensure avahi-daemon (publishes <hostname>.local) and avahi-utils (the resolver
# used for the collision probe) are installed, and the daemon is enabled unless
# services are being skipped. Best-effort: a failure just means LAN-by-IP only.
_ensure_avahi() {
  if ! dpkg -s avahi-daemon >/dev/null 2>&1; then
    say "Installing avahi-daemon for mDNS (<name>.local)"
    apt-get install -y --no-install-recommends avahi-daemon avahi-utils \
      || warn "avahi install failed; the box may only be reachable by IP"
  fi
  [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ] && return 0
  systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon enable failed"
}

# Echo a <base> or <base>-N that nothing else on the LAN already answers for.
# A name that resolves to our OWN IP counts as free, so a re-run keeps the same
# name instead of climbing the suffix every time. Fail-safe: no avahi, no
# network, or a timeout returns the base name unchanged rather than renaming.
_resolve_free_hostname() {
  local base="$1"
  command -v avahi-resolve-host-name >/dev/null 2>&1 || { printf '%s' "$base"; return 0; }
  # avahi must be up to answer queries; it currently publishes the OLD hostname,
  # so probing the DESIRED name is never a self-match yet.
  [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ] || systemctl start avahi-daemon >/dev/null 2>&1 || true
  local our_ips cand ip n
  our_ips=" $(hostname -I 2>/dev/null) "
  cand="$base"
  n=1
  while :; do
    ip="$(timeout 3 avahi-resolve-host-name -4 "$cand.local" 2>/dev/null | awk '{print $2}' | head -1)"
    [ -z "$ip" ] && break                          # nothing answers: free
    case "$our_ips" in *" $ip "*) break ;; esac     # answers to us: not a clash
    n=$((n + 1))
    cand="$base-$n"
    [ "$n" -gt 20 ] && break                        # give up climbing; use base-20
  done
  printf '%s' "$cand"
}

# Write the new system hostname to /etc/hostname and keep /etc/hosts consistent
# so local resolution (sudo, etc.) stays fast.
_set_system_hostname() {
  local name="$1"
  if command -v hostnamectl >/dev/null 2>&1; then
    hostnamectl set-hostname "$name" 2>/dev/null || printf '%s\n' "$name" > /etc/hostname
  else
    printf '%s\n' "$name" > /etc/hostname
    hostname "$name" 2>/dev/null || true
  fi
  if grep -qE '^127\.0\.1\.1' /etc/hosts 2>/dev/null; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$name/" /etc/hosts
  else
    printf '127.0.1.1\t%s\n' "$name" >> /etc/hosts
  fi
}

configure_mdns_hostname() {
  local base current
  base="${GLANCECAM_HOSTNAME:-glance}"
  current="$(hostname 2>/dev/null || echo unknown)"

  # Respect a hostname the user already chose: only take the name over when it
  # is the stock "raspberrypi" (or unset), OR when GLANCECAM_HOSTNAME explicitly
  # asked for one. A previously assigned "glance"/"glance-2" counts as "chosen"
  # here, so a plain re-run keeps its name instead of re-probing.
  if [ "$HOSTNAME_EXPLICIT" != "1" ]; then
    case "$current" in
      raspberrypi|localhost|""|unknown) : ;;   # stock/unset: ours to set
      *)
        say "Hostname already set to '$current'; leaving it"
        _ensure_avahi
        [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ] || systemctl try-restart avahi-daemon >/dev/null 2>&1 || true
        ok "Reachable at http://$current.local:9292"
        return 0
        ;;
    esac
  fi

  _ensure_avahi

  local name
  name="$(_resolve_free_hostname "$base")"
  if [ "$name" != "$base" ]; then
    say "Hostname '$base.local' is already used on this network; using '$name.local' instead"
  fi

  if [ "$name" != "$current" ]; then
    say "Setting hostname '$current' -> '$name'"
    _set_system_hostname "$name"
    # Republish under the final name (avahi was started publishing the old one
    # during the collision probe).
    [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ] || systemctl try-restart avahi-daemon >/dev/null 2>&1 || true
  else
    say "Hostname already '$name'"
  fi
  ok "Reachable at http://$name.local:9292 (or http://<this-host>:9292)"
}

# ==============================================================================
# Touch calibration (kiosk displays): line up taps with the rotated screen via a
# libinput calibration matrix, and enable the ADS7846 SPI overlay for resistive
# panels (GlanceCam-o4r). Called only when the kiosk is installed.
# ==============================================================================

# The Pi boot config path (Bookworm /boot/firmware, legacy /boot), or empty if
# neither exists (i.e. not a Pi).
_pi_config_txt() {
  if [ -f /boot/firmware/config.txt ]; then echo /boot/firmware/config.txt
  elif [ -f /boot/config.txt ]; then echo /boot/config.txt
  fi
}

# The six-float libinput matrix. An explicit GLANCECAM_TOUCH_MATRIX always wins;
# otherwise it follows GLANCECAM_ROTATION. Matrices mirror PantryRaider exactly.
_touch_matrix() {
  if [ -n "$GLANCECAM_TOUCH_MATRIX" ]; then
    printf '%s' "$GLANCECAM_TOUCH_MATRIX"
    return
  fi
  # printf '%s' (not a bare format string): the 180 matrix begins with "-1",
  # which printf would otherwise parse as an option flag.
  case "$GLANCECAM_ROTATION" in
    90)  printf '%s' '0 -1 1 1 0 0' ;;
    180) printf '%s' '-1 0 1 0 -1 1' ;;
    270) printf '%s' '0 1 0 -1 0 1' ;;
    *)   printf '%s' '1 0 0 0 1 0' ;;
  esac
}

configure_touch() {
  local driver="$GLANCECAM_TOUCH"

  if [ "$driver" = "none" ]; then
    say "Touch calibration disabled (GLANCECAM_TOUCH=none)"
    return 0
  fi

  # Auto-detect: a SPI bus points at an ADS7846 resistive panel (whose input
  # device does not exist until the overlay + SPI are on, so the bus is the
  # signal); otherwise an already-enumerated HID/I2C touchscreen. Explicit
  # ads7846/usb skip detection and always write the rule.
  if [ "$driver" = "auto" ]; then
    if ls /dev/spidev* >/dev/null 2>&1 || grep -qri 'ads7846' /sys/bus/spi/devices/ 2>/dev/null; then
      driver="ads7846"
    elif grep -qil 'touch' /sys/class/input/*/device/name 2>/dev/null; then
      driver="usb"
    else
      say "No touchscreen detected (GLANCECAM_TOUCH=auto); skipping touch calibration"
      return 0
    fi
    say "Auto-detected touch driver: $driver"
  fi

  # ADS7846 SPI panels report no input device until SPI and the ads7846 overlay
  # are enabled in the Pi boot config. Add both, guarded so a re-run never
  # duplicates the lines (and skipped off a Pi, where there is no config.txt).
  if [ "$driver" = "ads7846" ]; then
    local cfg
    cfg="$(_pi_config_txt)"
    if [ -z "$cfg" ]; then
      warn "No Pi boot config.txt found; cannot enable the ads7846 overlay"
    else
      if grep -q 'dtoverlay=ads7846' "$cfg" 2>/dev/null; then
        say "ads7846 overlay already present in $cfg; leaving it"
      else
        # penirq_pull=2 pulls up the active-low PENIRQ line; without it the line
        # floats and the controller registers no touches. Defaults suit the
        # Waveshare 3.5-4in HDMI panels; a specific panel may still need
        # GLANCECAM_TOUCH_MATRIX tuning.
        printf '\n# GlanceCam: ADS7846 SPI touch\ndtoverlay=ads7846,cs=1,penirq=25,penirq_pull=2,speed=50000,keep_vcc,pmax=255,xohms=150,xmin=200,xmax=3900,ymin=200,ymax=3900\n' >> "$cfg"
        say "Enabled the ads7846 overlay in $cfg (takes effect after reboot)"
      fi
      if ! grep -q 'dtparam=spi=on' "$cfg" 2>/dev/null; then
        printf 'dtparam=spi=on\n' >> "$cfg"
        say "Enabled SPI (dtparam=spi=on) in $cfg"
      fi
    fi
  fi

  local matrix
  matrix="$(_touch_matrix)"
  mkdir -p /etc/udev/rules.d
  # Match the panel by its touchscreen udev property rather than a device-name
  # glob: USB HID and ADS7846 panels report different names, but both carry
  # ID_INPUT_TOUCHSCREEN once enumerated, so one rule covers every panel.
  cat > /etc/udev/rules.d/99-glancecam-touch.rules <<EOF
# GlanceCam touchscreen calibration. libinput reads the affine transform from
# the LIBINPUT_CALIBRATION_MATRIX udev property; the six floats a b c d e f map
# touch coordinates onto the (possibly rotated) screen. The matrix follows
# GLANCECAM_ROTATION unless GLANCECAM_TOUCH_MATRIX overrides it.
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="$matrix"
EOF
  say "Wrote touch calibration (99-glancecam-touch.rules): matrix=$matrix driver=$driver"

  if [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ]; then
    warn "GLANCECAM_SKIP_SERVICES=1; not reloading udev rules"
    return 0
  fi
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --subsystem-match=input 2>/dev/null || true
}

# ==============================================================================
# Stream Deck controller (GlanceCam-psz install hook). The deck app itself is
# built under streamdeck/ by a separate step; this installs and wires it up.
# ==============================================================================

# Returns 0 if an Elgato Stream Deck (USB vendor 0fd9) is attached now.
_has_streamdeck() {
  [ -n "${FORCE_STREAMDECK:-}" ] && return 0   # test hook
  if command -v lsusb >/dev/null 2>&1; then
    lsusb 2>/dev/null | grep -qi '0fd9:' && return 0
  fi
  grep -qil '0fd9' /sys/bus/usb/devices/*/idVendor 2>/dev/null && return 0
  return 1
}

# The account the deck controller runs as: the interactive user, or the
# glancecam-kiosk user the kiosk step created when there is no interactive one.
_deck_user() {
  local u
  u="$(primary_user)"
  if [ -z "$u" ] && id -u glancecam-kiosk >/dev/null 2>&1; then
    u="glancecam-kiosk"
  fi
  printf '%s' "$u"
}

# Idempotent and re-run-safe. Called via `|| warn` so a failure here (a missing
# deck source, a pip error, no service user) never aborts the whole install.
install_streamdeck() {
  case "$GLANCECAM_STREAMDECK" in
    false) say "Stream Deck controller disabled (GLANCECAM_STREAMDECK=false)"; return 0 ;;
    true) : ;;
    auto)
      if ! _has_streamdeck; then
        say "No Stream Deck detected (GLANCECAM_STREAMDECK=auto); skipping"
        return 0
      fi ;;
  esac
  say "Installing the GlanceCam Stream Deck controller"

  local deck_src="$REPO_DIR/streamdeck"
  if [ ! -d "$deck_src" ]; then
    warn "Stream Deck sources not found at $deck_src; skipping deck install"
    return 0
  fi

  local venv="$INSTALL_DIR/deck-venv"
  local workdir="$INSTALL_DIR/deck"
  local unit_tpl="$deck_src/systemd/glancecam-streamdeck.service"
  local udev_src="$deck_src/udev/99-glancecam-streamdeck.rules"
  local req="$deck_src/requirements.txt"
  local conf_src="$deck_src/config.example.toml"
  local deck_conf="/etc/glancecam/streamdeck.toml"

  # USB HID access libraries for the python-elgato-streamdeck backend.
  apt-get install -y --no-install-recommends python3-venv libhidapi-libusb0 libudev-dev \
    || warn "Stream Deck USB dependency install had failures"

  if [ ! -x "$venv/bin/python" ]; then
    say "Creating the deck venv at $venv"
    python3 -m venv "$venv" || { warn "could not create the deck venv; skipping deck install"; return 0; }
  fi
  "$venv/bin/pip" install --upgrade pip --quiet || warn "deck pip upgrade failed"
  if [ -f "$req" ]; then
    "$venv/bin/pip" install --quiet -r "$req" || { warn "deck pip install failed; skipping deck service"; return 0; }
  else
    warn "no $req; skipping deck Python dependencies"
  fi

  # Copy the deck tree into place so `python -m glancecam_streamdeck` resolves
  # with WorkingDirectory pointed here. Replace any prior copy outright so a
  # re-run never nests it a level deeper.
  say "Installing deck sources to $workdir"
  rm -rf "$workdir"
  mkdir -p "$workdir"
  cp -a "$deck_src/." "$workdir/" || { warn "could not copy deck sources; skipping deck service"; return 0; }
  chmod -R a+rX "$workdir"

  # First-run config points the deck at the local app. Never clobber an edited
  # one (only written when absent).
  mkdir -p /etc/glancecam
  if [ ! -f "$deck_conf" ] && [ -f "$conf_src" ]; then
    cp -f "$conf_src" "$deck_conf"
    if grep -qE '^[[:space:]]*base_url[[:space:]]*=' "$deck_conf"; then
      sed -i -E 's#^[[:space:]]*base_url[[:space:]]*=.*#base_url = "http://127.0.0.1:9292"#' "$deck_conf"
    else
      # A bare top-level key must precede any [section] in TOML, so prepend it.
      sed -i '1i base_url = "http://127.0.0.1:9292"' "$deck_conf"
    fi
    say "Wrote $deck_conf (base_url http://127.0.0.1:9292)"
  fi

  local duser duid
  duser="$(_deck_user)"
  if [ -z "$duser" ]; then
    warn "No interactive user found for the Stream Deck service; leaving it installed but not enabled"
    return 0
  fi
  duid="$(id -u "$duser")"
  chown -R "$duser" "$workdir" "$venv" 2>/dev/null || true

  # The deck records its last selection under its data dir; create and own it
  # so the service user can write there (the config default is /var/lib/glancecam).
  mkdir -p /var/lib/glancecam 2>/dev/null || true
  chown "$duser" /var/lib/glancecam 2>/dev/null || true

  if [ ! -f "$unit_tpl" ]; then
    warn "Stream Deck unit template missing at $unit_tpl; skipping service"
    return 0
  fi
  sed \
    -e "s#__DECK_USER__#$duser#g" \
    -e "s#__DECK_UID__#$duid#g" \
    -e "s#__VENV__#$venv#g" \
    -e "s#__WORKDIR__#$workdir#g" \
    "$unit_tpl" > /etc/systemd/system/glancecam-streamdeck.service \
    || { warn "could not render the deck unit; skipping service"; return 0; }

  if [ -f "$udev_src" ]; then
    cp -f "$udev_src" /etc/udev/rules.d/99-glancecam-streamdeck.rules
  else
    warn "no $udev_src; the deck may need manual USB permissions"
  fi
  getent group plugdev >/dev/null 2>&1 && usermod -aG plugdev "$duser" 2>/dev/null || true

  # GLANCECAM_SKIP_SERVICES=1 leaves everything installed but touches neither
  # systemd nor udev (the CI smoke test runs with no systemd as PID 1).
  if [ "${GLANCECAM_SKIP_SERVICES:-0}" = "1" ]; then
    warn "GLANCECAM_SKIP_SERVICES=1; deck installed but not enabled/started"
    return 0
  fi
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --attr-match=idVendor=0fd9 2>/dev/null || true
  systemctl daemon-reload
  systemctl enable glancecam-streamdeck.service || warn "deck service enable failed"
  systemctl restart glancecam-streamdeck.service || warn "deck service (re)start failed (will retry on boot)"
  ok "Stream Deck controller installed"
}

install_native_mode() {
  ensure_native_deps
  fetch_repo
  ensure_glancecam_user
  install_go2rtc
  install_app_tree
  install_core_units
  # mDNS name is Pi-only (the appliance identity); a plain server keeps its own.
  if [ "$IS_PI" = true ]; then
    configure_mdns_hostname || warn "mDNS hostname setup did not complete"
  fi
  if [ "$ENABLE_KIOSK" = "true" ]; then
    install_kiosk
    # Touch calibration only makes sense with the kiosk on the display.
    configure_touch || warn "touch calibration did not complete"
  else
    say "Kiosk not enabled; GlanceCam is reachable over the LAN at http://<this-host>:9292"
  fi
  # Deck is gated on its own flag/detection; safe to try in any native install.
  install_streamdeck || warn "Stream Deck setup did not complete"
  ok "GlanceCam is running natively"
}

# -- main -----------------------------------------------------------------
main() {
  if [ "$GLANCECAM_MODE" = "docker" ]; then
    install_docker_mode
  else
    install_native_mode
  fi
  hr
  ok "Done. Open http://$(hostname -I 2>/dev/null | awk '{print $1}'):9292 in a browser to add your first camera."
  say "Run this same command again any time to update."
  hr
}

main
