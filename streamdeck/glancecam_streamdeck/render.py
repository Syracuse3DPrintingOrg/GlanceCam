"""Key image rendering with Pillow.

Every function here is pure: it takes sizes, bytes, and colours and returns a
plain PIL image, with no hardware import, so the whole module is exercised in
tests. The controller converts the returned image to the deck's native format.

A camera key is the latest snapshot cropped to the key, with the camera name on a
readable strip along the bottom. A camera with no frame yet (or an undecodable
one) falls back to a labelled "offline" placeholder, so a key is never blank
while a feed warms up.
"""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

# Label text height as a fraction of the key, and the floor it never drops below.
_LABEL_FRACTION = 0.20
_MIN_FONT_PX = 11
# Target label width as a fraction of the key width before shrinking kicks in.
_FIT_FRACTION = 0.92


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) != 6:
        return (60, 60, 60)
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (60, 60, 60)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start_px: int,
              max_width: int, floor: int):
    """The largest font (down to ``floor``) whose text fits ``max_width``."""
    size = max(floor, start_px)
    font = _font(size)
    while size > floor and _text_width(draw, text, font) > max_width:
        size -= 1
        font = _font(size)
    return font


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Trim ``text`` with an ellipsis until it fits ``max_width`` at ``font``."""
    if _text_width(draw, text, font) <= max_width:
        return text
    ell = "…"
    trimmed = text
    while trimmed and _text_width(draw, trimmed + ell, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ell) if trimmed else ell


def _center_crop_to_aspect(image: "Image.Image", target_w: int, target_h: int) -> "Image.Image":
    """Center-crop ``image`` to the aspect ratio of ``target_w`` x ``target_h``."""
    src_w, src_h = image.size
    if target_w <= 0 or target_h <= 0 or src_w <= 0 or src_h <= 0:
        return image
    # Cross-multiply to compare aspect ratios without float drift.
    if src_w * target_h > target_w * src_h:
        new_w = max(1, round(src_h * target_w / target_h))
        left = (src_w - new_w) // 2
        return image.crop((left, 0, left + new_w, src_h))
    new_h = max(1, round(src_w * target_h / target_w))
    top = (src_h - new_h) // 2
    return image.crop((0, top, src_w, top + new_h))


def image_from_jpeg(data: bytes, size: tuple[int, int]) -> "Image.Image | None":
    """Decode ``data`` into an RGB image cropped and resized to ``size``.

    Center-crops to the target aspect, then resizes to exactly ``size``. Returns
    None when the bytes are missing or cannot be decoded, so the draw loop can
    fall back to the offline face rather than crash.
    """
    if not data:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    try:
        with Image.open(io.BytesIO(data)) as src:
            src.load()
            image = src.convert("RGB")
    except (OSError, ValueError):
        return None
    cropped = _center_crop_to_aspect(image, width, height)
    return cropped.resize((width, height), Image.LANCZOS)


def _draw_label_strip(img: "Image.Image", name: str, label_color: str) -> None:
    """Paint the camera name on a translucent strip along the bottom of ``img``.

    The strip keeps the name legible over any picture; the text is trimmed with
    an ellipsis when the name is wider than the key.
    """
    if not name:
        return
    width, height = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    label_px = max(_MIN_FONT_PX, int(height * _LABEL_FRACTION))
    max_width = int(width * _FIT_FRACTION)
    font = _fit_font(draw, name, label_px, max_width, floor=_MIN_FONT_PX)
    text = _truncate(draw, name, font, max_width)
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    strip_h = th + max(2, int(height * 0.08))
    draw.rectangle([0, height - strip_h, width, height], fill=(0, 0, 0, 150))
    tx = (width - tw) / 2 - box[0]
    ty = height - strip_h + (strip_h - th) / 2 - box[1]
    draw.text((tx, ty), text, font=font, fill=_hex_to_rgb(label_color))


def compose_key(width: int, height: int, snapshot: bytes | None, name: str,
                *, background_color: str = "#101216", label_color: str = "#f4f4f5",
                offline_color: str = "#2a2d33") -> "Image.Image":
    """Compose one camera key: the snapshot with a name strip, or an offline face.

    When ``snapshot`` decodes it fills the key (center-cropped) with the name
    along the bottom. When it is missing or undecodable the key is the offline
    placeholder instead. Always returns an opaque RGB image of exactly
    ``width`` x ``height``.
    """
    frame = image_from_jpeg(snapshot, (width, height)) if snapshot else None
    if frame is None:
        return offline_key(width, height, name,
                           background_color=background_color,
                           label_color=label_color, offline_color=offline_color)
    _draw_label_strip(frame, name, label_color)
    return frame


def offline_key(width: int, height: int, name: str, *,
                background_color: str = "#101216", label_color: str = "#f4f4f5",
                offline_color: str = "#2a2d33") -> "Image.Image":
    """A placeholder for a camera with no frame yet: name over an 'Offline' note."""
    img = Image.new("RGB", (max(1, width), max(1, height)), _hex_to_rgb(offline_color))
    draw = ImageDraw.Draw(img)
    max_width = int(width * _FIT_FRACTION)
    fill = _hex_to_rgb(label_color)

    title = name or "Camera"
    title_px = max(_MIN_FONT_PX, int(height * 0.22))
    title_font = _fit_font(draw, title, title_px, max_width, floor=_MIN_FONT_PX)
    title = _truncate(draw, title, title_font, max_width)
    tbox = draw.textbbox((0, 0), title, font=title_font)
    tw, th = tbox[2] - tbox[0], tbox[3] - tbox[1]
    draw.text(((width - tw) / 2 - tbox[0], height * 0.30 - tbox[1]),
              title, font=title_font, fill=fill)

    note = "Offline"
    note_px = max(_MIN_FONT_PX, int(height * 0.16))
    note_font = _font(note_px)
    nbox = draw.textbbox((0, 0), note, font=note_font)
    nw, nh = nbox[2] - nbox[0], nbox[3] - nbox[1]
    muted = tuple(int(c * 0.6) for c in fill)
    draw.text(((width - nw) / 2 - nbox[0], height * 0.60 - nbox[1]),
              note, font=note_font, fill=muted)
    return img


def page_key(width: int, height: int, label: str = "More", *,
             accent_color: str = "#3b82f6", label_color: str = "#f4f4f5") -> "Image.Image":
    """The wrapping page-cycle key that appears when cameras overflow the deck."""
    img = Image.new("RGB", (max(1, width), max(1, height)), _hex_to_rgb(accent_color))
    draw = ImageDraw.Draw(img)
    max_width = int(width * _FIT_FRACTION)
    px = max(_MIN_FONT_PX, int(height * 0.26))
    font = _fit_font(draw, label, px, max_width, floor=_MIN_FONT_PX)
    box = draw.textbbox((0, 0), label, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((width - tw) / 2 - box[0], (height - th) / 2 - box[1]),
              label, font=font, fill=_hex_to_rgb(label_color))
    return img


def blank_key(width: int, height: int, background_color: str = "#101216") -> "Image.Image":
    return Image.new("RGB", (max(1, width), max(1, height)), _hex_to_rgb(background_color))


def message_across_deck(rows: int, cols: int, key_size: tuple[int, int], text: str,
                        *, background_color: str = "#101216",
                        label_color: str = "#f4f4f5") -> list["Image.Image"]:
    """Render a short ``text`` centred across the whole deck as per-key tiles.

    Used for the connecting splash and for a "No cameras" state so the deck shows
    a readable message rather than going blank. Returns ``rows*cols`` RGB tiles
    in row-major order, each ``key_size`` (w, h).
    """
    kw, kh = key_size
    rows = max(0, int(rows))
    cols = max(0, int(cols))
    if rows == 0 or cols == 0 or kw <= 0 or kh <= 0:
        return []
    full_w = cols * kw
    full_h = rows * kh
    canvas = Image.new("RGB", (full_w, full_h), _hex_to_rgb(background_color))
    draw = ImageDraw.Draw(canvas)
    px = max(_MIN_FONT_PX, int(full_h * 0.18))
    font = _fit_font(draw, text, px, int(full_w * _FIT_FRACTION), floor=_MIN_FONT_PX)
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((full_w - tw) / 2 - box[0], (full_h - th) / 2 - box[1]),
              text, font=font, fill=_hex_to_rgb(label_color))
    tiles: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            tiles.append(canvas.crop((c * kw, r * kh, c * kw + kw, r * kh + kh)))
    return tiles
