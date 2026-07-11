"""Hardware detection and the stream budget.

``detect_hardware`` is implemented for real (device tree model, CPU count,
total RAM). ``budget`` is a generous placeholder: the real per-surface math
(how many live tiles a given Pi or desktop can decode without dropping frames)
is owned by the resources agent in a later wave. See the TODO on ``budget``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


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
        if "raspberry pi 5" in low:
            return "pi5"
        if "raspberry pi 4" in low:
            return "pi4"
        if "raspberry pi 3" in low:
            return "pi3"
        if "raspberry pi" in low:
            # An older or unrecognized Pi: treat as the most constrained class.
            return "pi3"
    # Not a Pi. Assume an x86-class host if it has real RAM; else unknown.
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


def budget(hardware: Optional[dict] = None, client_hint: Optional[dict] = None) -> dict:
    """How many live tiles a surface should decode at once.

    TODO(resources agent): replace this placeholder with the real per-surface
    math (budget by pixel rate and hardware class, per the design doc: a Pi 3
    around 2 HD tiles, a Pi 4 around 4-6 sub streams, a Pi 5 and x86 higher; a
    remote browser gets a generous default because it decodes client-side). For
    now every surface gets a generous fixed limit so nothing is throttled before
    that math lands.
    """
    if hardware is None:
        hardware = detect_hardware()
    return {
        "live_tile_limit": 9,
        "reasons": [],
        "recommendations": [],
    }
