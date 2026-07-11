"""Hardware detection and the stream budget.

``detect_hardware`` parses the device tree model (Pi generation, including
Compute Modules) and falls back to ``platform.machine()`` for non-Pi hosts.
``budget`` turns a camera list plus a hardware class (or a remote client's
hint) into a live-tile count: how many cameras a surface can decode at once
before the rest fall back to refreshing snapshots. The math is pure (no I/O
beyond ``detect_hardware``'s file reads) so it is fully unit-testable.
"""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Optional

# Assumed frame rate for a "live" grid tile. Cameras rarely matter above this
# for a quick glance, so cost is computed at this rate regardless of the
# camera's actual configured fps.
_ASSUMED_FPS = 15

# Decode budget per hardware class, in megapixels-per-second (pixels/sec of
# decoded video the class can sustain across all live tiles combined).
_CLASS_BUDGET_MPS = {
    "pi_zero": 30.0,
    "pi3": 30.0,
    "pi4": 90.0,
    "pi5": 180.0,
    "x86": 400.0,
    "unknown": 60.0,
}

# User-forward hardware names for recommendation copy.
_CLASS_LABEL = {
    "pi_zero": "Raspberry Pi Zero",
    "pi3": "Raspberry Pi 3",
    "pi4": "Raspberry Pi 4",
    "pi5": "Raspberry Pi 5",
    "x86": "this computer",
    "unknown": "this device",
}

# Defaults when a camera's resolution has not been probed yet.
_DEFAULT_SUB_RESOLUTION = (640, 360)
_DEFAULT_MAIN_RESOLUTION = (1920, 1080)

# Hard sanity cap regardless of how generous the budget math gets: nobody
# wants a 20-up grid on a glance viewer.
_HARD_TILE_LIMIT = 16

# A remote client's decode budget scales with its reported core count. Even a
# single-core client gets a usable minimum.
_CLIENT_MPS_PER_CORE = 60.0
_CLIENT_MPS_MIN = 120.0

# Below this tile width (px) in the resulting grid, tiles are considered too
# small to be useful and a recommendation is surfaced.
_SMALL_TILE_WIDTH_PX = 320


def _pi_model() -> Optional[str]:
    try:
        raw = Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return None
    # The device tree string is NUL-terminated; strip trailing NULs/whitespace.
    return raw.replace("\x00", "").strip() or None


def _total_ram_mb() -> int:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return 0
    m = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.MULTILINE)
    return int(m.group(1)) // 1024 if m else 0


def _classify(model: Optional[str], ram_mb: int) -> str:
    if model:
        low = model.lower()
        # Compute Modules share the SoC of their numbered generation, so they
        # get the same class as the matching Pi board.
        if "raspberry pi 5" in low or "compute module 5" in low:
            return "pi5"
        if "raspberry pi 4" in low or "compute module 4" in low:
            return "pi4"
        if "raspberry pi 3" in low or "compute module 3" in low:
            return "pi3"
        if "raspberry pi zero" in low:
            return "pi_zero"
        if "raspberry pi" in low:
            # An older or unrecognized Pi: treat as the most constrained class.
            return "pi3"
    # Not a Pi. Classify by CPU architecture; unknown/exotic arches with real
    # RAM still get treated as a generic host rather than "unknown".
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "i386", "i686", "aarch64", "arm64", "armv7l", "armv8l"):
        if ram_mb >= 1024:
            return "x86"
    if ram_mb >= 1024:
        return "x86"
    return "unknown"


def detect_hardware() -> dict:
    """Detect the host: Pi model (if any), CPU count, total RAM, rough class."""
    model = _pi_model()
    ram_mb = _total_ram_mb()
    return {
        "model": model,
        "cpu_count": os.cpu_count() or 1,
        "ram_mb": ram_mb,
        "class": _classify(model, ram_mb),
    }


def _camera_cost(camera: dict) -> tuple[float, str]:
    """A camera's decode cost in megapixels/sec, and which stream it uses."""
    sub_res = camera.get("sub_resolution")
    if sub_res:
        w, h = sub_res
        return (w * h * _ASSUMED_FPS) / 1_000_000, "sub"
    main_res = camera.get("main_resolution")
    if main_res:
        w, h = main_res
        return (w * h * _ASSUMED_FPS) / 1_000_000, "main"
    if camera.get("sub_url"):
        # A sub stream is configured but not yet probed: assume the default
        # sub resolution rather than penalizing it as a full HD main stream.
        w, h = _DEFAULT_SUB_RESOLUTION
        return (w * h * _ASSUMED_FPS) / 1_000_000, "sub"
    w, h = _DEFAULT_MAIN_RESOLUTION
    return (w * h * _ASSUMED_FPS) / 1_000_000, "main"


def _grid_dims(count: int) -> tuple[int, int]:
    """Rows/cols for a roughly-square grid of ``count`` tiles."""
    import math
    if count <= 0:
        return (0, 0)
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return rows, cols


def budget(cameras: Optional[list] = None, hardware: Optional[dict] = None,
           client_hint: Optional[dict] = None) -> dict:
    """How many live tiles a surface should decode at once.

    ``cameras`` is the ordered camera list (design doc order = grid order);
    live slots are assigned greedily in that order until the budget runs out,
    with at least one camera always live. ``client_hint`` (cores/width/height)
    signals a remote browser surface; its presence takes priority over
    ``hardware`` for the decode budget, since a remote browser decodes
    client-side and the host's own hardware class is irrelevant to it.
    """
    cameras = cameras or []
    reasons: list[str] = []
    recommendations: list[str] = []

    is_remote = bool(client_hint and client_hint.get("cores"))
    if is_remote:
        cores = max(1, int(client_hint["cores"]))
        mps_budget = max(_CLIENT_MPS_MIN, cores * _CLIENT_MPS_PER_CORE)
        surface = "remote"
        reasons.append(f"Remote browser reporting {cores} CPU cores.")
    else:
        hardware = hardware or detect_hardware()
        hw_class = hardware.get("class", "unknown")
        mps_budget = _CLASS_BUDGET_MPS.get(hw_class, _CLASS_BUDGET_MPS["unknown"])
        surface = "kiosk"
        label = _CLASS_LABEL.get(hw_class, "this device")
        reasons.append(f"Detected hardware class: {hw_class} ({label}).")

    per_camera: dict[str, dict] = {}
    used_mps = 0.0
    live_count = 0
    no_sub_names: list[str] = []

    for i, cam in enumerate(cameras):
        cost, uses = _camera_cost(cam)
        cid = cam.get("id", f"cam_{i}")
        name = cam.get("name", cid)
        if uses == "main" and not cam.get("sub_url") and not cam.get("sub_resolution"):
            no_sub_names.append(name)

        fits = (used_mps + cost) <= mps_budget and live_count < _HARD_TILE_LIMIT
        if live_count == 0:
            # Always allow at least one live tile, even if it alone exceeds
            # the budget (a single oversized stream is still better than none).
            fits = True
        if fits:
            per_camera[cid] = {"live": True, "uses": uses, "cost": cost}
            used_mps += cost
            live_count += 1
        else:
            per_camera[cid] = {"live": False, "uses": uses, "cost": cost}

    for name in no_sub_names:
        recommendations.append(
            f"{name} has no sub stream and uses a full HD slot in the grid; "
            "add its sub channel in camera settings if available."
        )

    total = len(cameras)
    if live_count < total:
        if is_remote:
            recommendations.append(
                f"This browser can smoothly show about {live_count} live "
                f"cameras at once; the rest show refreshing snapshots until "
                "you tap them live."
            )
        else:
            label = _CLASS_LABEL.get(hardware.get("class", "unknown"), "This device") if hardware else "This device"
            recommendations.append(
                f"{label} can decode about {live_count} low-res streams "
                "smoothly; remaining cameras show refreshing snapshots."
            )
    elif total > 0:
        recommendations.append("All cameras fit within this device's decode budget.")

    if is_remote and client_hint.get("width"):
        width = client_hint["width"]
        rows, cols = _grid_dims(live_count)
        if cols > 0:
            tile_width = width / cols
            if tile_width < _SMALL_TILE_WIDTH_PX:
                recommendations.append(
                    f"With {live_count} cameras on this screen, tiles are "
                    "small; consider a second display or grouping."
                )

    return {
        "live_tile_limit": live_count,
        "surface": surface,
        "reasons": reasons,
        "recommendations": recommendations,
        "per_camera": per_camera,
    }
