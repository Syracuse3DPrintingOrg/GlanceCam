"""Pure Pillow rendering: camera keys, the offline placeholder, and JPEG decode."""
from __future__ import annotations

import io
import sys
from pathlib import Path

_SD = Path(__file__).resolve().parent.parent / "streamdeck"
if str(_SD) not in sys.path:
    sys.path.insert(0, str(_SD))

from PIL import Image  # noqa: E402

from glancecam_streamdeck import render  # noqa: E402

KEY = (96, 96)


def _jpeg_bytes(size=(320, 180), color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_compose_key_with_snapshot_is_right_size():
    img = render.compose_key(*KEY, _jpeg_bytes(), "Front Door")
    assert isinstance(img, Image.Image)
    assert img.size == KEY
    assert img.mode == "RGB"


def test_compose_key_without_snapshot_falls_back_to_offline():
    img = render.compose_key(*KEY, None, "Back Yard")
    assert img.size == KEY
    assert img.mode == "RGB"


def test_compose_key_with_undecodable_bytes_is_offline():
    img = render.compose_key(*KEY, b"not a jpeg", "Garage")
    assert img.size == KEY
    assert img.mode == "RGB"


def test_image_from_jpeg_decodes_and_resizes():
    img = render.image_from_jpeg(_jpeg_bytes(), KEY)
    assert img is not None
    assert img.size == KEY


def test_image_from_jpeg_returns_none_on_bad_input():
    assert render.image_from_jpeg(b"", KEY) is None
    assert render.image_from_jpeg(b"garbage", KEY) is None
    assert render.image_from_jpeg(_jpeg_bytes(), (0, 96)) is None


def test_offline_key_handles_empty_name():
    img = render.offline_key(*KEY, "")
    assert img.size == KEY


def test_page_key_is_right_size():
    img = render.page_key(*KEY, "More")
    assert img.size == KEY


def test_blank_key_uses_background_color():
    img = render.blank_key(*KEY, "#0a0b0c")
    assert img.size == KEY
    assert img.getpixel((0, 0)) == (10, 11, 12)


def test_message_across_deck_slices_into_tiles():
    tiles = render.message_across_deck(3, 5, KEY, "GlanceCam")
    assert len(tiles) == 15  # rows * cols
    assert all(t.size == KEY for t in tiles)


def test_message_across_deck_handles_degenerate_grid():
    assert render.message_across_deck(0, 5, KEY, "x") == []
    assert render.message_across_deck(3, 5, (0, 0), "x") == []


def test_long_name_still_renders_within_the_key():
    # A name far wider than the key must not raise; it is trimmed to fit.
    img = render.compose_key(*KEY, _jpeg_bytes(), "A very long camera name that overflows")
    assert img.size == KEY
