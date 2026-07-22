"""On-screen Home Assistant event channel for the kiosk grid.

Home Assistant pushes events here (an automation's rest_command), and the kiosk
grid polls for them and acts on them:

- a camera pop-up expands that camera to fullscreen for a few seconds
- a notify shows a small toast on the display

Events live in a small ring capped by count and age, so a kiosk that was off
does not get flooded with a backlog when it polls. Each event carries a
monotonically increasing ``id`` so a client polls for "what is new since the
last id I saw" without missing or replaying events.

The ring persists to ``ha_events.json`` under data_dir through the shared atomic
StateFile, so it survives a restart and multiple workers agree on it. The
read-modify-write of an add is guarded by a module lock (the cameras.py
pattern); GlanceCam's kiosk is a single worker, so a cross-process flock is not
needed here. Polling never writes the file: the age/size prune is applied in
memory for the answer and re-applied on the next add.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from ..config import settings
from ..statefile import StateFile

# Keep only the most recent events, and drop anything older than the TTL.
_MAX_EVENTS = 50
_TTL_SECONDS = 120

_VALID_LEVELS = ("info", "success", "warning", "error")

_lock = threading.Lock()
_store: Optional[StateFile] = None
_store_path: Optional[Path] = None


def _get_store() -> StateFile:
    """The StateFile for the current data_dir, rebuilt if data_dir changed.

    Resolved lazily (not at import) so tests that repoint data_dir get a fresh
    store rather than one bound to the import-time path.
    """
    global _store, _store_path
    path = Path(settings.data_dir) / "ha_events.json"
    if _store is None or _store_path != path:
        _store = StateFile(path, default={"next": 1, "events": []})
        _store_path = path
    return _store


# ---- Pure helpers (unit-tested without touching the store) -----------------

def normalize_level(level: str) -> str:
    """Coerce a notify level to one of info/success/warning/error (default info)."""
    lvl = (level or "").strip().lower()
    return lvl if lvl in _VALID_LEVELS else "info"


def clamp_seconds(seconds) -> int:
    """A non-negative integer duration; anything unparseable becomes 0."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return 0
    return max(0, s)


def _prune(events: list, now: float) -> list:
    """Drop events past the TTL, then keep at most the newest ``_MAX_EVENTS``.

    Pure, so the count cap and age cutoff are tested with synthetic timestamps
    and no wall-clock flakiness.
    """
    cutoff = now - _TTL_SECONDS
    kept = [e for e in events
            if isinstance(e, dict) and float(e.get("ts", 0)) >= cutoff]
    return kept[-_MAX_EVENTS:]


# ---- Ring mutation and reads -----------------------------------------------

def _add(event: dict) -> int:
    """Append an event under the shared lock and persist. Returns its id."""
    now = time.time()
    with _lock:
        store = _get_store()
        data = store.read()
        nxt = int(data.get("next", 1)) if isinstance(data, dict) else 1
        events = list(data.get("events", [])) if isinstance(data, dict) else []
        event = dict(event)
        event["id"] = nxt
        event["ts"] = now
        events.append(event)
        events = _prune(events, now)
        # Write a fresh document; never mutate the StateFile's cached object.
        store.write({"next": nxt + 1, "events": events})
        return event["id"]


def add_camera_popup(camera_id: str, seconds: int = 0, name: str = "") -> int:
    """Queue a camera pop-up. ``camera_id`` is the concrete GlanceCam camera id
    the grid pops to fullscreen; ``name`` is only carried for display."""
    return _add({
        "type": "camera",
        "camera_id": str(camera_id or ""),
        "name": str(name or ""),
        "seconds": clamp_seconds(seconds),
    })


def add_notify(message: str, level: str = "info") -> int:
    """Queue a notification toast for the display."""
    return _add({
        "type": "notify",
        "message": str(message or ""),
        "level": normalize_level(level),
    })


def poll(since_id: int = 0) -> dict:
    """Events newer than ``since_id``, plus the current last id.

    A fresh client should first read ``last_id`` (poll with a huge since) so it
    only sees events that arrive after it connects, rather than replaying the
    ring on load. Never writes the file.
    """
    now = time.time()
    with _lock:
        data = _get_store().read()
        nxt = int(data.get("next", 1)) if isinstance(data, dict) else 1
        events = list(data.get("events", [])) if isinstance(data, dict) else []
        events = _prune(events, now)
        try:
            after = int(since_id)
        except (TypeError, ValueError):
            after = 0
        fresh = [dict(e) for e in events if int(e.get("id", 0)) > after]
    return {"events": fresh, "last_id": nxt - 1}


def last_id() -> int:
    with _lock:
        data = _get_store().read()
        return (int(data.get("next", 1)) - 1) if isinstance(data, dict) else 0


def reset() -> None:
    """Clear the ring and drop the state file (used by tests)."""
    global _store, _store_path
    with _lock:
        try:
            (Path(settings.data_dir) / "ha_events.json").unlink(missing_ok=True)
        except OSError:
            pass
        _store = None
        _store_path = None
