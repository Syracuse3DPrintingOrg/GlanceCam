"""Rotation mapping and camera-to-key paging for the Stream Deck controller."""
from __future__ import annotations

import sys
from pathlib import Path

_SD = Path(__file__).resolve().parent.parent / "streamdeck"
if str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

import pytest  # noqa: E402

from glancecam_streamdeck import layout  # noqa: E402


@pytest.mark.parametrize("key_count", sorted(layout.GRID))
@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_rotated_index_is_a_bijection(key_count, rotation):
    mapped = [layout.rotated_index(i, key_count, rotation) for i in range(key_count)]
    # Every visual slot lands on a distinct physical key covering the whole deck.
    assert sorted(mapped) == list(range(key_count))


@pytest.mark.parametrize("key_count", sorted(layout.GRID))
@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_slot_for_physical_inverts_rotated_index(key_count, rotation):
    for slot in range(key_count):
        phys = layout.rotated_index(slot, key_count, rotation)
        assert layout.slot_for_physical(phys, key_count, rotation) == slot


def test_display_dims_swaps_on_quarter_turns():
    assert layout.display_dims(32, 0) == (8, 4)
    assert layout.display_dims(32, 180) == (8, 4)
    assert layout.display_dims(32, 90) == (4, 8)
    assert layout.display_dims(32, 270) == (4, 8)


def test_unknown_key_count_is_identity():
    assert layout.rotated_index(3, 7, 90) == 3
    assert layout.slot_for_physical(3, 7, 90) == 3


def test_build_pages_single_page_pads_with_blanks():
    pages = layout.build_camera_pages(["a", "b"], 6)
    assert len(pages) == 1
    assert pages[0] == ["a", "b", None, None, None, None]


def test_build_pages_blank_string_is_a_blank_slot():
    pages = layout.build_camera_pages(["a", "", "b"], 6)
    assert pages[0] == ["a", None, "b", None, None, None]


def test_build_pages_overflow_adds_page_key():
    # Seven cameras on a 6-key deck: the last slot of each page becomes More.
    ids = [f"c{i}" for i in range(7)]
    pages = layout.build_camera_pages(ids, 6)
    assert len(pages) == 2
    assert pages[0][:5] == ["c0", "c1", "c2", "c3", "c4"]
    assert pages[0][5] == layout.PAGE_NEXT
    assert pages[1][0] == "c5"
    assert pages[1][1] == "c6"
    assert pages[1][-1] == layout.PAGE_NEXT


def test_resolve_slots_prefers_configured():
    assert layout.resolve_slots(["a", "", "b"], ["x", "y"]) == ["a", "", "b"]


def test_resolve_slots_auto_fills_from_live_list():
    assert layout.resolve_slots([], ["x", "y", "z"]) == ["x", "y", "z"]


def test_build_pages_rejects_bad_key_count():
    with pytest.raises(ValueError):
        layout.build_camera_pages(["a"], 0)
