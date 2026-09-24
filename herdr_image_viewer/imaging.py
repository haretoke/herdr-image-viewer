"""Image conversion and resizing through the platform's tools.

macOS has sips, Linux containers ImageMagick; ffmpeg is used when present.
Herdr draws pixels 1:1, so every image is resized to its exact rendered size.
"""

import struct

from . import png

IEND = png.chunk(b"IEND", b"")


class ImagingError(Exception):
    pass


def png_size(data):
    """(width, height) of a complete PNG; truncated or malformed data is refused."""
    if len(data) < 24 or not data.startswith(png.SIGNATURE) or data[12:16] != b"IHDR":
        raise ImagingError("not a PNG")
    if not data.endswith(IEND):
        raise ImagingError("PNG is truncated")
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        raise ImagingError("PNG has no pixels")
    return width, height
