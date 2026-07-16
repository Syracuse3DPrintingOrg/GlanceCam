"""The camera store, persisted to data_dir/cameras.json.

A camera is a plain dict (see ``_ALLOWED_FIELDS`` and the design data model).
Credentials never leave the server: ``public_view`` replaces a non-empty
username/password with the ``"__set__"`` sentinel, and ``update`` treats an
incoming ``"__set__"`` as "keep the stored value". The store is backed by the
shared atomic StateFile so multiple workers agree and it survives a restart.
"""
from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from ..statefile import StateFile

# The sentinel returned in place of a stored secret, and accepted back on an
# update to mean "leave the stored value alone".
SECRET_SENTINEL = "__set__"

# The only keys a camera dict may carry. Anything else in an incoming payload is
# dropped so a stray field can never be persisted.
_ALLOWED_FIELDS = {
    "id", "name", "enabled", "order", "source",
    "main_url", "sub_url", "snapshot_url",
    "username", "password", "ha_entity",
    "main_resolution", "sub_resolution",
    "main_codec", "sub_codec",
    "fullscreen_uses_main", "allow_self_signed",
}

# Sources that do not carry a direct RTSP main_url (their feed is resolved from
# a token or entity instead).
_URL_OPTIONAL_SOURCES = {"homeassistant"}

_lock = threading.Lock()
_store: Optional[StateFile] = None
_store_path: Optional[Path] = None


class CameraError(ValueError):
    """Raised on invalid camera input (missing name or main_url)."""


def _get_store() -> StateFile:
    """The StateFile for the current data_dir, rebuilt if data_dir changed.

    Resolved lazily (not at import) so tests that repoint data_dir get a fresh
    store rather than one bound to the import-time path.
    """
    global _store, _store_path
    path = Path(settings.data_dir) / "cameras.json"
    if _store is None or _store_path != path:
        _store = StateFile(path, default={"cameras": []})
        _store_path = path
    return _store


def _read_all() -> list[dict]:
    data = _get_store().read()
    cams = data.get("cameras") if isinstance(data, dict) else None
    return list(cams) if isinstance(cams, list) else []


def _write_all(cams: list[dict]) -> None:
    _get_store().write({"cameras": cams})


def _new_id(existing: list[dict]) -> str:
    taken = {c.get("id") for c in existing}
    while True:
        cid = "cam_" + secrets.token_hex(3)  # 6 hex chars
        if cid not in taken:
            return cid


def _clean(data: dict) -> dict:
    """Keep only allowed keys from an incoming payload."""
    return {k: v for k, v in data.items() if k in _ALLOWED_FIELDS}


def _normalize_new(data: dict) -> dict:
    """Build a full camera dict from a cleaned add payload, filling defaults."""
    cam = _clean(data)
    cam.setdefault("enabled", True)
    cam.setdefault("source", "manual")
    cam.setdefault("sub_url", None)
    cam.setdefault("snapshot_url", None)
    cam.setdefault("username", "")
    cam.setdefault("password", "")
    cam.setdefault("ha_entity", None)
    cam.setdefault("main_resolution", None)
    cam.setdefault("sub_resolution", None)
    cam.setdefault("main_codec", None)
    cam.setdefault("sub_codec", None)
    cam.setdefault("fullscreen_uses_main", settings.fullscreen_uses_main)
    return cam


def _validate(cam: dict) -> None:
    if not str(cam.get("name", "")).strip():
        raise CameraError("A camera needs a name.")
    source = str(cam.get("source", "manual"))
    if source not in _URL_OPTIONAL_SOURCES and not str(cam.get("main_url", "")).strip():
        raise CameraError("A camera needs a main stream address.")


def public_view(camera: dict) -> dict:
    """A copy safe to send to the browser: secrets replaced with the sentinel.

    A non-empty username or password becomes ``"__set__"`` so the UI can show
    "a value is set" without ever receiving it; an empty one stays empty.
    """
    out = dict(camera)
    for field in ("username", "password"):
        if out.get(field):
            out[field] = SECRET_SENTINEL
    return out


def list_cameras() -> list[dict]:
    """All cameras, ordered by their ``order`` field (stored copies)."""
    cams = _read_all()
    return sorted(cams, key=lambda c: c.get("order", 0))


def get(camera_id: str) -> Optional[dict]:
    for c in _read_all():
        if c.get("id") == camera_id:
            return c
    return None


def add(data: dict) -> dict:
    """Create a camera from ``data``, assign an id and the next order, persist."""
    with _lock:
        cams = _read_all()
        cam = _normalize_new(data)
        _validate(cam)
        cam["id"] = _new_id(cams)
        orders = [c.get("order", 0) for c in cams]
        cam["order"] = (max(orders) + 1) if orders else 0
        cams.append(cam)
        _write_all(cams)
        return cam


def update(camera_id: str, data: dict) -> Optional[dict]:
    """Merge cleaned ``data`` into an existing camera and persist.

    A ``"__set__"`` sentinel for username/password keeps the stored secret.
    Returns the updated camera, or None if the id is unknown.
    """
    with _lock:
        cams = _read_all()
        for i, c in enumerate(cams):
            if c.get("id") != camera_id:
                continue
            patch = _clean(data)
            patch.pop("id", None)  # id is immutable
            for field in ("username", "password"):
                if patch.get(field) == SECRET_SENTINEL:
                    patch.pop(field)  # keep the stored secret
            merged = {**c, **patch}
            _validate(merged)
            cams[i] = merged
            _write_all(cams)
            return merged
        return None


def probe_patch(camera: dict, main_probe: Optional[dict],
                sub_probe: Optional[dict]) -> dict:
    """The resolution/codec fields a go2rtc probe would backfill, without writing.

    Reads each stream's probe (``{"codec": str|None, "resolution": [w, h]|None}``
    from ``go2rtc.parse_probe``) and returns only the keys whose value is newly
    known and differs from what the camera already holds. An empty dict means
    nothing changed. Pure, so the merge rules are unit tested with fixture probe
    dicts and no go2rtc. A probe reading ``None`` (stream offline, unparseable)
    never clears a value the camera already learned.
    """
    patch: dict = {}
    for prefix, probe in (("main", main_probe), ("sub", sub_probe)):
        if not isinstance(probe, dict):
            continue
        res = probe.get("resolution")
        if (isinstance(res, list) and len(res) == 2
                and camera.get(f"{prefix}_resolution") != res):
            patch[f"{prefix}_resolution"] = res
        codec = probe.get("codec")
        if codec and camera.get(f"{prefix}_codec") != codec:
            patch[f"{prefix}_codec"] = codec
    return patch


def backfill(camera_id: str, main_probe: Optional[dict],
             sub_probe: Optional[dict]) -> Optional[dict]:
    """Store any newly-probed resolution/codec for a camera and persist.

    Writes only when ``probe_patch`` finds a change, so a repeated probe of an
    already-known camera does not churn the store. Returns the camera (updated
    or unchanged), or None if the id is unknown.
    """
    with _lock:
        cams = _read_all()
        for i, c in enumerate(cams):
            if c.get("id") != camera_id:
                continue
            patch = probe_patch(c, main_probe, sub_probe)
            if not patch:
                return c
            merged = {**c, **patch}
            cams[i] = merged
            _write_all(cams)
            return merged
        return None


def remove(camera_id: str) -> bool:
    with _lock:
        cams = _read_all()
        kept = [c for c in cams if c.get("id") != camera_id]
        if len(kept) == len(cams):
            return False
        _write_all(kept)
        return True


def reorder(ids: list[str]) -> list[dict]:
    """Reassign ``order`` to match the given id sequence.

    Ids present in the store but missing from ``ids`` keep a stable order after
    the listed ones, so a partial list never drops cameras.
    """
    with _lock:
        cams = _read_all()
        by_id = {c.get("id"): c for c in cams}
        order = 0
        for cid in ids:
            if cid in by_id:
                by_id[cid]["order"] = order
                order += 1
        for c in cams:
            if c.get("id") not in ids:
                c["order"] = order
                order += 1
        _write_all(cams)
        return sorted(cams, key=lambda c: c.get("order", 0))
