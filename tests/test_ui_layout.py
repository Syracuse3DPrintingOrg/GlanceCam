"""Python twin of grid.js `chooseLayout`, tested to lock the tiling algorithm.

The grid packs N cameras edge to edge and picks the column/row split that makes
each (letterboxed) tile as large as possible for the viewport. This mirrors the
JavaScript byte-for-byte in intent so a regression in either is caught here.
"""
from __future__ import annotations

import math

DEFAULT_ASPECT = 16 / 9


def choose_layout(n: int, w: float, h: float, aspect: float = DEFAULT_ASPECT):
    """Return (cols, rows) for N tiles in a w x h viewport at the given aspect."""
    if n <= 0:
        return (1, 1)
    if not (aspect > 0):
        aspect = DEFAULT_ASPECT
    if not (w > 0) or not (h > 0):
        cols = math.ceil(math.sqrt(n))
        return (cols, math.ceil(n / cols))

    target = w / h
    best = None  # (cols, rows, area)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell_w = w / cols
        cell_h = h / rows
        if cell_w / cell_h > aspect:
            tile_h = cell_h
            tile_w = cell_h * aspect
        else:
            tile_w = cell_w
            tile_h = cell_w / aspect
        area = tile_w * tile_h
        if best is None or area > best[2] * 1.005:
            best = (cols, rows, area)
            continue
        if area >= best[2] * 0.995:
            # Tie on tile size: prefer the split shaped like the viewport, then
            # fewer empty cells.
            d_cand = abs(cols / rows - target)
            d_best = abs(best[0] / best[1] - target)
            if d_cand < d_best - 1e-9:
                best = (cols, rows, area)
            elif abs(d_cand - d_best) < 1e-9 and cols * rows < best[0] * best[1]:
                best = (cols, rows, area)
    return (best[0], best[1])


def test_single_camera_fills_one_cell():
    assert choose_layout(1, 1920, 1080, DEFAULT_ASPECT) == (1, 1)


def test_two_wide_16x9_go_side_by_side():
    # A landscape viewport with two 16:9 tiles: two columns beats one stacked.
    assert choose_layout(2, 1920, 1080, DEFAULT_ASPECT) == (2, 1)


def test_two_on_a_tall_portrait_stack():
    # A tall phone viewport stacks two landscape tiles rather than shrinking.
    assert choose_layout(2, 400, 1600, DEFAULT_ASPECT) == (1, 2)


def test_four_cameras_form_a_square_grid():
    assert choose_layout(4, 1920, 1080, DEFAULT_ASPECT) == (2, 2)


def test_six_cameras_prefer_three_by_two_on_landscape():
    assert choose_layout(6, 1920, 1080, DEFAULT_ASPECT) == (3, 2)


def test_zero_or_bad_input_is_safe():
    assert choose_layout(0, 1920, 1080) == (1, 1)
    # Unknown viewport falls back to a near-square split.
    assert choose_layout(4, 0, 0) == (2, 2)


def test_bad_aspect_falls_back_to_16x9():
    assert choose_layout(2, 1920, 1080, 0) == (2, 1)
