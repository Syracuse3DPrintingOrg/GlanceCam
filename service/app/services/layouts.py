"""Saved grid layouts, persisted to data_dir/layouts.json.

A layout is a named, first-class arrangement of tiles on the display:

    {"id": "lay_ab12cd", "name": "Front of house",
     "cols": 3, "rows": 2,
     "cells": [{"camera_id": "cam_...", "col": 0, "row": 0, "w": 2, "h": 1}, ...]}

``col``/``row`` are zero-based; ``w``/``h`` are spans in grid cells. The document
also tracks which layout is active ("auto" means the automatic packer, an id
means that saved layout). A future on-screen menu switches the active layout, so
layouts are stored and served like cameras and credentials rather than derived.

Unknown ``camera_id`` values are allowed on purpose: a camera may be deleted
after a layout references it, and the grid simply skips a cell whose camera is
gone rather than the layout becoming invalid.
"""
from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from ..statefile import StateFile

# The builder offers 1..6 columns and rows; the backend holds saved layouts to
# the same bound so a hand-posted layout cannot exceed what the UI can show.
MAX_SPAN = 6

_lock = threading.Lock()
_store: Optional[StateFile] = None
_store_path: Optional[Path] = None

_DEFAULT = {"active": "auto", "layouts": []}


def _get_store() -> StateFile:
    """The StateFile for the current data_dir, rebuilt if data_dir changed.

    Resolved lazily (not at import) so tests that repoint data_dir get a fresh
    store rather than one bound to the import-time path.
    """
    global _store, _store_path
    path = Path(settings.data_dir) / "layouts.json"
    if _store is None or _store_path != path:
        _store = StateFile(path, default=dict(_DEFAULT))
        _store_path = path
    return _store


def _read_doc() -> dict:
    data = _get_store().read()
    if not isinstance(data, dict):
        return dict(_DEFAULT)
    layouts = data.get("layouts")
    return {
        "active": data.get("active") or "auto",
        "layouts": list(layouts) if isinstance(layouts, list) else [],
    }


def _write_doc(doc: dict) -> None:
    _get_store().write(doc)


def _new_id(existing: list[dict]) -> str:
    taken = {c.get("id") for c in existing}
    while True:
        lid = "lay_" + secrets.token_hex(3)  # 6 hex chars
        if lid not in taken:
            return lid


def validate_layout(layout: Any) -> Optional[str]:
    """Return a human problem string if ``layout`` is unusable, else None.

    Pure and side-effect free so both the router and the tests exercise the same
    rules the builder mirrors in the browser: spans stay in bounds and no two
    cells overlap. Unknown camera ids are not an error here.
    """
    if not isinstance(layout, dict):
        return "A layout must be an object."
    try:
        cols = int(layout.get("cols"))
        rows = int(layout.get("rows"))
    except (TypeError, ValueError):
        return "A layout needs whole-number columns and rows."
    if not (1 <= cols <= MAX_SPAN):
        return f"Columns must be between 1 and {MAX_SPAN}."
    if not (1 <= rows <= MAX_SPAN):
        return f"Rows must be between 1 and {MAX_SPAN}."

    cells = layout.get("cells")
    if not isinstance(cells, list):
        return "A layout needs a list of cells."

    occupied: set[tuple[int, int]] = set()
    for cell in cells:
        if not isinstance(cell, dict):
            return "Each tile must be an object."
        try:
            col = int(cell.get("col"))
            row = int(cell.get("row"))
            w = int(cell.get("w", 1))
            h = int(cell.get("h", 1))
        except (TypeError, ValueError):
            return "A tile has a bad position or size."
        if w < 1 or h < 1:
            return "A tile must be at least one cell wide and tall."
        if col < 0 or row < 0 or col + w > cols or row + h > rows:
            return "A tile falls outside the grid."
        for cc in range(col, col + w):
            for rr in range(row, row + h):
                key = (cc, rr)
                if key in occupied:
                    return "Two tiles overlap."
                occupied.add(key)
    return None


def _clean_layout(layout: dict) -> dict:
    """Keep only the stored shape from an incoming payload."""
    cells = []
    for cell in layout.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        cells.append({
            "camera_id": cell.get("camera_id"),
            "col": int(cell.get("col")),
            "row": int(cell.get("row")),
            "w": int(cell.get("w", 1)),
            "h": int(cell.get("h", 1)),
        })
    return {
        "name": str(layout.get("name") or "").strip() or "Layout",
        "cols": int(layout.get("cols")),
        "rows": int(layout.get("rows")),
        "cells": cells,
    }


def list_layouts() -> list[dict]:
    """All saved layouts (stored copies)."""
    return list(_read_doc()["layouts"])


def get(layout_id: str) -> Optional[dict]:
    for lay in _read_doc()["layouts"]:
        if lay.get("id") == layout_id:
            return lay
    return None


def active_layout() -> Optional[dict]:
    """The active saved layout, or None when automatic (or the id is stale)."""
    doc = _read_doc()
    active = doc.get("active")
    if not active or active == "auto":
        return None
    for lay in doc["layouts"]:
        if lay.get("id") == active:
            return lay
    return None


def active_id() -> str:
    """The active layout id, or "auto"."""
    return _read_doc().get("active") or "auto"


def save_layout(layout: dict) -> dict:
    """Create or update a layout. Raises ValueError with a problem on bad input.

    An incoming ``id`` that matches a stored layout updates it in place; anything
    else creates a new layout with a fresh ``lay_`` id.
    """
    problem = validate_layout(layout)
    if problem:
        raise ValueError(problem)
    with _lock:
        doc = _read_doc()
        layouts = doc["layouts"]
        cleaned = _clean_layout(layout)
        incoming_id = layout.get("id")
        for i, lay in enumerate(layouts):
            if lay.get("id") == incoming_id:
                cleaned["id"] = incoming_id
                layouts[i] = cleaned
                _write_doc({"active": doc["active"], "layouts": layouts})
                return cleaned
        cleaned["id"] = _new_id(layouts)
        layouts.append(cleaned)
        _write_doc({"active": doc["active"], "layouts": layouts})
        return cleaned


def remove(layout_id: str) -> bool:
    """Delete a layout. If it was active, fall back to the automatic packer."""
    with _lock:
        doc = _read_doc()
        layouts = doc["layouts"]
        kept = [lay for lay in layouts if lay.get("id") != layout_id]
        if len(kept) == len(layouts):
            return False
        active = doc["active"]
        if active == layout_id:
            active = "auto"
        _write_doc({"active": active, "layouts": kept})
        return True


def set_active(id_or_auto: str) -> str:
    """Point the display at a saved layout or the automatic packer ("auto").

    Raises ValueError if an id is given that no saved layout carries.
    """
    with _lock:
        doc = _read_doc()
        if id_or_auto != "auto":
            if not any(lay.get("id") == id_or_auto for lay in doc["layouts"]):
                raise ValueError("No such layout.")
        _write_doc({"active": id_or_auto, "layouts": doc["layouts"]})
        return id_or_auto
