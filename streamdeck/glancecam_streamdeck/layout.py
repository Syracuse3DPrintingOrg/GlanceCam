"""Key layout, paging, and rotation mapping for whatever deck is plugged in.

A deck has a fixed number of keys (Mini 6, Original/MK.2 15, XL 32). Cameras are
laid onto the keys in reading order; when there are more cameras than keys the
last key of each page becomes a wrapping "More" key and the rest spill onto the
next page. Rotation is handled the same way the PantryRaider deck does it: a
visual slot (the grid the web editor draws) maps to the physical key it lands on
after the deck is turned, and the inverse recovers the slot a press belongs to.

Everything here is pure: it takes plain lists and ints and returns plain lists
and ints, so the tiling and the rotation bijection are exercised in unit tests
without any hardware.
"""
from __future__ import annotations

from typing import Optional

# A slot value meaning "the page-cycle key" rather than a camera or a blank.
PAGE_NEXT = "__page_next__"

# A blank key is an empty string in the configured key list; internally a page
# slot uses None for a blank so a camera id is never confused with "no camera".
BLANK = ""

# Physical grid (columns, rows) for each known deck size.
GRID: dict[int, tuple[int, int]] = {
    6: (3, 2),    # Stream Deck Mini
    15: (5, 3),   # Stream Deck / MK.2
    32: (8, 4),   # Stream Deck XL
}


def supported_key_counts() -> tuple[int, ...]:
    return tuple(sorted(GRID))


def display_dims(key_count: int, rotation: int) -> tuple[int, int]:
    """The (cols, rows) of the grid as the user sees it after rotating.

    For 0 and 180 the deck keeps its native shape. For 90 and 270 it is turned
    on its side, so columns and rows swap (an 8x4 XL becomes a 4x8 portrait).
    """
    cols, rows = GRID[key_count]
    if rotation in (90, 270):
        return rows, cols
    return cols, rows


def rotated_index(index: int, key_count: int, rotation: int) -> int:
    """Map a visual slot to the physical key it lands on after rotation.

    ``index`` is a slot in row-major order of the displayed grid. We recover its
    (row, col) using the displayed dimensions, rigidly turn that coordinate
    clockwise by ``rotation`` into the deck's native grid (the same turn the draw
    loop applies to each key image), and flatten to a physical key. The map is an
    exact bijection for all four rotations, so every slot lands on a distinct key.
    """
    if rotation == 0 or key_count not in GRID:
        return index
    p_cols, p_rows = GRID[key_count]
    d_cols, d_rows = display_dims(key_count, rotation)
    if not (0 <= index < d_cols * d_rows):
        return index
    vr, vc = divmod(index, d_cols)
    if rotation == 180:
        pr, pc = p_rows - 1 - vr, p_cols - 1 - vc
    elif rotation == 90:
        pr, pc = vc, d_rows - 1 - vr
    else:  # 270
        pr, pc = d_cols - 1 - vc, vr
    return pr * p_cols + pc


def slot_for_physical(phys: int, key_count: int, rotation: int) -> int:
    """Inverse of :func:`rotated_index`: a physical key to its displayed slot.

    Used when a key is pressed: the device reports the physical index, and this
    recovers which slot the user sees there so the right camera is selected.
    """
    if rotation == 0 or key_count not in GRID:
        return phys
    p_cols, p_rows = GRID[key_count]
    d_cols, d_rows = display_dims(key_count, rotation)
    if not (0 <= phys < p_cols * p_rows):
        return phys
    pr, pc = divmod(phys, p_cols)
    if rotation == 180:
        vr, vc = p_rows - 1 - pr, p_cols - 1 - pc
    elif rotation == 90:
        vr, vc = d_rows - 1 - pc, pr
    else:  # 270
        vr, vc = pc, d_cols - 1 - pr
    return vr * d_cols + vc


def resolve_slots(configured_keys: list[str], live_camera_ids: list[str]) -> list[str]:
    """Decide the ordered per-slot camera ids to lay out.

    With a non-empty ``configured_keys`` the saved assignment is honoured verbatim
    (a blank stays blank; a camera id that no longer exists still gets a slot and
    renders as an offline placeholder, so the grid does not silently reshuffle).
    With no assignment the live camera list fills the slots in its saved order.
    """
    if configured_keys:
        return list(configured_keys)
    return list(live_camera_ids)


def build_camera_pages(slots: list[str], key_count: int) -> list[list[Optional[str]]]:
    """Split per-slot camera ids into deck-sized pages.

    Each returned slot is a camera id, ``None`` for a blank key, or
    :data:`PAGE_NEXT` for the wrapping page-cycle key. With everything fitting on
    one page no key is sacrificed for paging; when the slots overflow the final
    key of every page becomes the "More" key and the rest continue on the next
    page. An empty-string slot is a blank, preserving the positions around it.
    """
    if key_count < 1:
        raise ValueError("key_count must be positive")
    resolved: list[Optional[str]] = [None if s == BLANK else s for s in slots]
    if len(resolved) <= key_count:
        page = list(resolved) + [None] * (key_count - len(resolved))
        return [page]
    usable = key_count - 1
    pages: list[list[Optional[str]]] = []
    for start in range(0, len(resolved), usable):
        chunk = resolved[start:start + usable]
        page = list(chunk) + [None] * (usable - len(chunk))
        page.append(PAGE_NEXT)
        pages.append(page)
    return pages
