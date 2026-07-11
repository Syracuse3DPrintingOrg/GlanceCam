"""Saved credential sets, persisted to data_dir/credentials.json.

A camera login is often the same across several cameras, so the user can save a
named set once and apply it when probing, previewing, or adding a camera. An
entry is ``{id, name, username, password}``. Passwords never leave the server:
``list_public`` replaces a stored password with the ``"__set__"`` sentinel, the
same pattern the camera store uses, and ``resolve`` is the only path that hands
the real password back (server-side, to reach a device).

Backed by the shared atomic StateFile so multiple workers agree and the sets
survive a restart.
"""
from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Optional

from ..config import settings
from ..statefile import StateFile

# Shown in place of a stored password in any browser-facing response.
SECRET_SENTINEL = "__set__"

_lock = threading.Lock()
_store: Optional[StateFile] = None
_store_path: Optional[Path] = None


def _get_store() -> StateFile:
    """The StateFile for the current data_dir, rebuilt if data_dir changed.

    Resolved lazily (not at import) so a test that repoints data_dir gets a
    fresh store rather than one bound to the import-time path.
    """
    global _store, _store_path
    path = Path(settings.data_dir) / "credentials.json"
    if _store is None or _store_path != path:
        _store = StateFile(path, default={"credentials": []})
        _store_path = path
    return _store


def _read_all() -> list[dict]:
    data = _get_store().read()
    creds = data.get("credentials") if isinstance(data, dict) else None
    return list(creds) if isinstance(creds, list) else []


def _write_all(creds: list[dict]) -> None:
    _get_store().write({"credentials": creds})


def _new_id(existing: list[dict]) -> str:
    taken = {c.get("id") for c in existing}
    while True:
        cid = "cred_" + secrets.token_hex(3)  # 6 hex chars
        if cid not in taken:
            return cid


def public(entry: dict) -> dict:
    """A copy safe to send to the browser: the password is the sentinel."""
    out = {"id": entry.get("id"), "name": entry.get("name", ""),
           "username": entry.get("username", "")}
    out["password"] = SECRET_SENTINEL if entry.get("password") else ""
    return out


def list_public() -> list[dict]:
    """Every saved set with its password masked, ordered by name."""
    return [public(c) for c in sorted(_read_all(),
                                      key=lambda c: str(c.get("name", "")).lower())]


def add(name: str, username: str, password: str) -> dict:
    """Save a new credential set. Returns its public (masked) view."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A credential set needs a name.")
    with _lock:
        creds = _read_all()
        entry = {"id": _new_id(creds), "name": name,
                 "username": username or "", "password": password or ""}
        creds.append(entry)
        _write_all(creds)
        return public(entry)


def update(cred_id: str, name=None, username=None, password=None) -> Optional[dict]:
    """Update fields of a saved set. A ``"__set__"`` password keeps the stored
    one; None means "leave this field alone". Returns the masked view or None."""
    with _lock:
        creds = _read_all()
        for c in creds:
            if c.get("id") != cred_id:
                continue
            if name is not None:
                c["name"] = str(name).strip() or c.get("name", "")
            if username is not None:
                c["username"] = username
            if password is not None and password != SECRET_SENTINEL:
                c["password"] = password
            _write_all(creds)
            return public(c)
        return None


def remove(cred_id: str) -> bool:
    with _lock:
        creds = _read_all()
        kept = [c for c in creds if c.get("id") != cred_id]
        if len(kept) == len(creds):
            return False
        _write_all(kept)
        return True


def resolve(cred_id: str) -> Optional[tuple[str, str]]:
    """The real ``(username, password)`` for a saved set, or None if unknown.

    Server-side only: this is the one path that returns the actual password, so
    a probe/preview/add can reach the device.
    """
    if not cred_id:
        return None
    for c in _read_all():
        if c.get("id") == cred_id:
            return c.get("username", "") or "", c.get("password", "") or ""
    return None
