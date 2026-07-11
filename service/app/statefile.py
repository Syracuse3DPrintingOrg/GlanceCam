"""Generic atomic, mtime-gated JSON state file.

Cross-surface state (the camera list, and later any shared kiosk state) lives
in small JSON files under data_dir so multiple uvicorn workers agree and the
state survives a restart. This helper is the shared mechanism:

- Writes go to a temp file in the same directory and are renamed over the
  target (``os.replace``), so a crash mid-write never truncates the live file.
- Reads stat the file's mtime and only re-parse when it changed, so a hot read
  path costs one stat call once the value is cached.
- A threading.Lock guards the cache so concurrent workers in one process do not
  race.
- If the directory is not writable (tests, a read-only mount) the helper
  quietly degrades to in-memory behavior instead of raising.

The pattern mirrors PantryRaider's per-feature state files, generalized so
every consumer shares one tested implementation.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class StateFile:
    """An atomic, mtime-cached JSON document at ``path``."""

    def __init__(self, path: str | os.PathLike, default: Any = None) -> None:
        self._path = Path(path)
        self._default = default if default is not None else {}
        self._lock = threading.Lock()
        self._cache: Any = None
        self._mtime: int | None = None
        # In-memory fallback used only when the directory is unwritable.
        self._mem: Any = None

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> Any:
        """Return the current document, re-parsing only when the file changed.

        Returns a deep-ish copy semantics note: callers get the cached object,
        so treat the result as read-only or copy before mutating."""
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime_ns
            except OSError:
                # No file yet, or unwritable dir: serve the in-memory value if we
                # have one, else the default.
                if self._mem is not None:
                    return self._mem
                return json.loads(json.dumps(self._default))
            if mtime == self._mtime and self._cache is not None:
                return self._cache
            try:
                data = json.loads(self._path.read_text())
            except (OSError, ValueError):
                # A torn or corrupt read never breaks a caller; keep what we have.
                if self._cache is not None:
                    return self._cache
                return json.loads(json.dumps(self._default))
            self._cache = data
            self._mtime = mtime
            return data

    def write(self, obj: Any) -> None:
        """Persist ``obj`` atomically. Degrades to an in-memory copy silently if
        the directory cannot be written."""
        with self._lock:
            self._mem = obj
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_name(self._path.name + ".tmp")
                tmp.write_text(json.dumps(obj, indent=2))
                os.replace(tmp, self._path)
                self._cache = obj
                self._mtime = self._path.stat().st_mtime_ns
            except OSError:
                # data_dir not writable: keep the value in memory for this process.
                self._cache = obj
                self._mtime = None
