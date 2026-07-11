"""Saved-layout store: validation rules and the create/update/active lifecycle."""
import pytest

from app.services import layouts as store


# ---- validate_layout (pure) ------------------------------------------------

def _layout(cols, rows, cells):
    return {"name": "L", "cols": cols, "rows": rows, "cells": cells}


def test_valid_layout_passes():
    lay = _layout(3, 2, [
        {"camera_id": "duo", "col": 0, "row": 0, "w": 2, "h": 1},
        {"camera_id": "a", "col": 2, "row": 0, "w": 1, "h": 1},
        {"camera_id": "b", "col": 0, "row": 1, "w": 1, "h": 1},
    ])
    assert store.validate_layout(lay) is None


def test_out_of_bounds_span_is_rejected():
    lay = _layout(2, 2, [{"camera_id": "a", "col": 1, "row": 0, "w": 2, "h": 1}])
    assert store.validate_layout(lay) is not None


def test_overlapping_cells_rejected():
    lay = _layout(2, 2, [
        {"camera_id": "a", "col": 0, "row": 0, "w": 2, "h": 1},
        {"camera_id": "b", "col": 1, "row": 0, "w": 1, "h": 1},
    ])
    assert store.validate_layout(lay) == "Two tiles overlap."


def test_columns_out_of_range_rejected():
    assert store.validate_layout(_layout(7, 2, [])) is not None
    assert store.validate_layout(_layout(0, 2, [])) is not None


def test_unknown_camera_id_is_allowed():
    lay = _layout(1, 1, [{"camera_id": "cam_gone", "col": 0, "row": 0, "w": 1, "h": 1}])
    assert store.validate_layout(lay) is None


# ---- store lifecycle -------------------------------------------------------

def test_save_creates_then_updates(data_dir):
    a = store.save_layout(_layout(2, 1, [{"camera_id": "x", "col": 0, "row": 0, "w": 1, "h": 1}]))
    assert a["id"].startswith("lay_") and len(a["id"]) == 10
    a["name"] = "Renamed"
    b = store.save_layout(a)
    assert b["id"] == a["id"]
    assert b["name"] == "Renamed"
    assert len(store.list_layouts()) == 1


def test_save_rejects_invalid(data_dir):
    with pytest.raises(ValueError):
        store.save_layout(_layout(2, 2, [
            {"camera_id": "a", "col": 0, "row": 0, "w": 2, "h": 1},
            {"camera_id": "b", "col": 1, "row": 0, "w": 1, "h": 1},
        ]))


def test_active_defaults_to_auto(data_dir):
    assert store.active_id() == "auto"
    assert store.active_layout() is None


def test_set_active_and_delete_falls_back_to_auto(data_dir):
    lay = store.save_layout(_layout(1, 1, []))
    store.set_active(lay["id"])
    assert store.active_id() == lay["id"]
    assert store.active_layout()["id"] == lay["id"]
    store.remove(lay["id"])
    assert store.active_id() == "auto"


def test_set_active_unknown_id_raises(data_dir):
    with pytest.raises(ValueError):
        store.set_active("lay_nope")
