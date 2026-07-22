"""The controller's pure pieces: the selection signal file and backoff.

The controller module imports the Stream Deck device library lazily inside the
functions that touch hardware, so importing it here (with no deck and no
``StreamDeck`` wheel installed) is safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SD = Path(__file__).resolve().parent.parent / "streamdeck"
if str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

from glancecam_streamdeck import controller  # noqa: E402


def test_write_selection_creates_parent_and_atomic_json(tmp_path):
    path = tmp_path / "nested" / "deck-selection.json"
    assert controller.write_selection(path, "cam_123", "Front Door", ts=42.0) is True

    data = json.loads(path.read_text())
    assert data["camera_id"] == "cam_123"
    assert data["name"] == "Front Door"
    assert data["ts"] == 42.0
    assert data["source"] == "streamdeck"
    # No temp file is left behind after the atomic replace.
    assert not (path.parent / (path.name + ".tmp")).exists()


def test_write_selection_overwrites_previous(tmp_path):
    path = tmp_path / "deck-selection.json"
    controller.write_selection(path, "cam_a", "A", ts=1.0)
    controller.write_selection(path, "cam_b", "B", ts=2.0)
    data = json.loads(path.read_text())
    assert data["camera_id"] == "cam_b"
    assert data["ts"] == 2.0


def test_write_selection_returns_false_on_error(tmp_path):
    # A path whose parent is a file, not a directory, cannot be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = blocker / "child" / "sel.json"
    assert controller.write_selection(bad, "cam_a", "A") is False


def test_next_backoff_grows_and_caps():
    first = controller._next_backoff(0.0)
    assert first == controller.RECONNECT_BACKOFF_START
    assert controller._next_backoff(first) == first * controller.RECONNECT_BACKOFF_FACTOR
    assert controller._next_backoff(1000.0) == controller.RECONNECT_BACKOFF_MAX
