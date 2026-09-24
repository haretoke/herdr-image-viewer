"""Minimal PNG encoder: 8-bit RGB, non-interlaced, filter 0 on every row."""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_rgb(width, height, pixels, level=6):
    stride = width * 3
    if len(pixels) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGB, got {len(pixels)}")
    rows = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (
        SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level))
        + chunk(b"IEND", b"")
    )
