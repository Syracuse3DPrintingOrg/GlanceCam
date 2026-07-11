"""Python twin of grid.js `packAuto`, tested to lock the mixed-aspect packer.

The automatic grid now accommodates ultrawide cameras (a Reolink Duo is about
32:9). A camera whose aspect is >= 2.4 is "wide" and takes a 2-column, 1-row
cell; everything else is 1x1. `packAuto` chooses the column/row split that makes
tiles as large as possible (same area rule as `choose_layout`, measured for a
normal 16:9 tile in one cell), places the wide tiles first across the top rows,
then fills the rest with normal tiles.

This mirrors the JavaScript in intent so a regression in either is caught here.
"""
from __future__ import annotations

import math

from test_ui_layout import choose_layout  # the existing uniform-grid twin

DEFAULT_ASPECT = 16 / 9
WIDE_ASPECT = 2.4  # >= this is treated as ultrawide (Reolink Duo, 21:9 panels)


def _tile_area(cell_w: float, cell_h: float, aspect: float) -> float:
    """Area of a tile of the given aspect fit (contain) inside a cell."""
    if cell_w / cell_h > aspect:
        tile_h = cell_h
        tile_w = cell_h * aspect
    else:
        tile_w = cell_w
        tile_h = cell_w / aspect
    return tile_w * tile_h


def _place(cols: int, wide_ids: list[str], normal_ids: list[str]):
    """Fill a `cols`-wide grid: wide (2x1) tiles first, then normal (1x1).

    Rows grow as needed. Both passes scan row-major and take the first free
    spot, so wide tiles land top-left to bottom-right and normals fill the gaps.
    Returns (cells, rows_used).
    """
    occ: list[list[bool]] = []

    def ensure(r: int) -> None:
        while len(occ) <= r:
            occ.append([False] * cols)

    cells = []

    def place_wide(cid: str) -> None:
        r = 0
        while True:
            ensure(r)
            for c in range(cols - 1):
                if not occ[r][c] and not occ[r][c + 1]:
                    occ[r][c] = occ[r][c + 1] = True
                    cells.append({"camera_id": cid, "col": c, "row": r, "w": 2, "h": 1})
                    return
            r += 1

    def place_normal(cid: str) -> None:
        r = 0
        while True:
            ensure(r)
            for c in range(cols):
                if not occ[r][c]:
                    occ[r][c] = True
                    cells.append({"camera_id": cid, "col": c, "row": r, "w": 1, "h": 1})
                    return
            r += 1

    for cid in wide_ids:
        place_wide(cid)
    for cid in normal_ids:
        place_normal(cid)
    return cells, len(occ)


def pack_auto(cameras: list[dict], w: float, h: float) -> dict:
    """Return {cols, rows, cells} packing `cameras` (each {camera_id, aspect})."""
    wide_ids = [c["camera_id"] for c in cameras if c.get("aspect", DEFAULT_ASPECT) >= WIDE_ASPECT]
    normal_ids = [c["camera_id"] for c in cameras if c.get("aspect", DEFAULT_ASPECT) < WIDE_ASPECT]
    demand = 2 * len(wide_ids) + len(normal_ids)
    if demand <= 0:
        return {"cols": 1, "rows": 1, "cells": []}
    if not (w > 0) or not (h > 0):
        w, h = 16.0, 9.0  # a sane shape when the viewport is unknown
    target = w / h

    min_cols = 2 if wide_ids else 1
    best = None  # (cols, rows, area, cells)
    for cols in range(min_cols, demand + 1):
        cells, rows = _place(cols, wide_ids, normal_ids)
        area = _tile_area(w / cols, h / rows, DEFAULT_ASPECT)
        cand = (cols, rows, area, cells)
        if best is None or area > best[2] * 1.005:
            best = cand
            continue
        if area >= best[2] * 0.995:
            # Tie on tile size: prefer the split shaped like the viewport, then
            # the one with fewer empty cells.
            d_cand = abs(cols / rows - target)
            d_best = abs(best[0] / best[1] - target)
            if d_cand < d_best - 1e-9:
                best = cand
            elif abs(d_cand - d_best) < 1e-9 and cols * rows < best[0] * best[1]:
                best = cand
    return {"cols": best[0], "rows": best[1], "cells": best[3]}


# ---- Fixtures --------------------------------------------------------------

def _by_pos(cells):
    return {c["camera_id"]: (c["col"], c["row"], c["w"], c["h"]) for c in cells}


def test_duo_plus_three_normal_on_16x9():
    cams = [
        {"camera_id": "duo", "aspect": 32 / 9},
        {"camera_id": "a", "aspect": 16 / 9},
        {"camera_id": "b", "aspect": 16 / 9},
        {"camera_id": "c", "aspect": 16 / 9},
    ]
    out = pack_auto(cams, 1920, 1080)
    assert (out["cols"], out["rows"]) == (3, 2)
    pos = _by_pos(out["cells"])
    # The duo spans the top-left two cells; normals fill the rest.
    assert pos["duo"] == (0, 0, 2, 1)
    assert pos["a"] == (2, 0, 1, 1)
    assert pos["b"] == (0, 1, 1, 1)
    assert pos["c"] == (1, 1, 1, 1)


def test_duo_alone_spans_full_width():
    out = pack_auto([{"camera_id": "duo", "aspect": 32 / 9}], 1920, 1080)
    assert (out["cols"], out["rows"]) == (2, 1)
    assert _by_pos(out["cells"])["duo"] == (0, 0, 2, 1)


def test_all_normal_matches_old_uniform_grid():
    # With no wide cameras the packer must reproduce the old uniform split.
    for n in (1, 2, 3, 4, 5, 6, 7, 9):
        cams = [{"camera_id": f"c{i}", "aspect": 16 / 9} for i in range(n)]
        out = pack_auto(cams, 1920, 1080)
        assert (out["cols"], out["rows"]) == choose_layout(n, 1920, 1080, DEFAULT_ASPECT)
        # Every camera is placed exactly once, in bounds, without overlap.
        assert len(out["cells"]) == n
        seen = set()
        for cell in out["cells"]:
            assert 0 <= cell["col"] < out["cols"]
            assert 0 <= cell["row"] < out["rows"]
            assert (cell["col"], cell["row"]) not in seen
            seen.add((cell["col"], cell["row"]))


def test_portrait_viewport_stacks_a_duo_pair():
    # A tall viewport with two duos should stack them (rows over columns).
    cams = [{"camera_id": "d1", "aspect": 32 / 9}, {"camera_id": "d2", "aspect": 32 / 9}]
    out = pack_auto(cams, 400, 1600)
    assert (out["cols"], out["rows"]) == (2, 2)
    pos = _by_pos(out["cells"])
    assert pos["d1"] == (0, 0, 2, 1)
    assert pos["d2"] == (0, 1, 2, 1)


def test_empty_is_safe():
    out = pack_auto([], 1920, 1080)
    assert out == {"cols": 1, "rows": 1, "cells": []}
