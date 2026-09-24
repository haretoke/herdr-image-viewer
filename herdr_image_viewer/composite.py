"""One RGB image holding the visible thumbnails.

Thumbnails are flattened onto the background once and kept in a normal and a
dimmed variant, so drawing a page only copies rows (no per-pixel Python work).
The selected thumbnail is drawn normal with a highlight border; the others are
dimmed.
"""

from dataclasses import dataclass

BACKGROUND = (24, 24, 24)  # a gray: dimming uses one translate table for all channels
HIGHLIGHT = (255, 196, 0)
BORDER_PX = 2
DIM_FACTOR = 0.35
DIM_TABLE = bytes(
    round(BACKGROUND[0] + (value - BACKGROUND[0]) * DIM_FACTOR) for value in range(256)
)


@dataclass(frozen=True)
class Slot:
    """A cell box on the canvas, in pixels."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Thumb:
    width: int
    height: int
    normal: bytes
    dimmed: bytes


def prepare(width, height, rgba):
    """Flatten an RGBA thumbnail once and derive its dimmed variant."""
    rgb = flatten(rgba)
    return Thumb(width, height, rgb, rgb.translate(DIM_TABLE))


def flatten(rgba):
    rgb = bytearray(len(rgba) // 4 * 3)
    rgb[0::3] = rgba[0::4]
    rgb[1::3] = rgba[1::4]
    rgb[2::3] = rgba[2::4]
    return bytes(rgb)


def render(width, height, slots, thumbs, selected):
    """RGB bytes of a width x height canvas with each thumb centered in its slot."""
    canvas = bytearray(bytes(BACKGROUND) * (width * height))
    for index, (slot, thumb) in enumerate(zip(slots, thumbs)):
        if thumb is None:
            continue
        pixels = thumb.normal if index == selected else thumb.dimmed
        x = slot.x + (slot.width - thumb.width) // 2
        y = slot.y + (slot.height - thumb.height) // 2
        row_bytes = thumb.width * 3
        for row in range(thumb.height):
            start = ((y + row) * width + x) * 3
            canvas[start:start + row_bytes] = pixels[row * row_bytes:(row + 1) * row_bytes]
        if index == selected:
            draw_border(canvas, width, slot)
    return bytes(canvas)


def draw_border(canvas, width, slot):
    full = bytes(HIGHLIGHT) * slot.width
    side = bytes(HIGHLIGHT) * BORDER_PX
    for row in range(slot.y, slot.y + slot.height):
        start = (row * width + slot.x) * 3
        if row < slot.y + BORDER_PX or row >= slot.y + slot.height - BORDER_PX:
            canvas[start:start + len(full)] = full
        else:
            canvas[start:start + len(side)] = side
            end = start + (slot.width - BORDER_PX) * 3
            canvas[end:end + len(side)] = side
