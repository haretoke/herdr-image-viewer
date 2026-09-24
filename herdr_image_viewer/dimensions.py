"""Pixel dimensions declared by image headers, read without decoding, so the
pixel cap is enforced before any converter allocates the image."""

import os
import struct

from .safety import UnsafeInput

# JPEG start-of-frame markers; 0xC4 (DHT), 0xC8 (JPG), and 0xCC (DAC) share the range.
JPEG_FRAMES = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}


def image_size(handle, image_format):
    """(width, height) from the header of the binary file handle."""
    try:
        handle.seek(0)
        return READERS[image_format](handle)
    except (struct.error, ValueError, KeyError) as error:
        raise UnsafeInput(f"cannot read the size of this {image_format} image") from error


def read_exact(handle, count):
    data = handle.read(count)
    if len(data) != count:
        raise ValueError("truncated header")
    return data


def png_size(handle):
    return struct.unpack(">II", read_exact(handle, 24)[16:24])


def gif_size(handle):
    return struct.unpack("<HH", read_exact(handle, 10)[6:10])


def bmp_size(handle):
    header = read_exact(handle, 26)
    (dib_size,) = struct.unpack("<I", header[14:18])
    if dib_size == 12:  # BITMAPCOREHEADER
        return struct.unpack("<HH", header[18:22])
    width, height = struct.unpack("<ii", header[18:26])
    return width, abs(height)  # negative height: rows stored top-down


def webp_size(handle):
    kind = read_exact(handle, 16)[12:16]
    if kind == b"VP8 ":
        header = read_exact(handle, 14)  # chunk size, frame tag, start code, sizes
        if header[7:10] != b"\x9d\x01\x2a":
            raise ValueError("no VP8 start code")
        width, height = struct.unpack("<HH", header[10:14])
        return width & 0x3FFF, height & 0x3FFF  # the top bits are scaling hints
    if kind == b"VP8L":
        header = read_exact(handle, 9)  # chunk size, signature, packed sizes
        if header[4] != 0x2F:
            raise ValueError("no VP8L signature")
        (bits,) = struct.unpack("<I", header[5:9])
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        header = read_exact(handle, 14)  # chunk size, flags, sizes minus one
        return int.from_bytes(header[8:11], "little") + 1, int.from_bytes(header[11:14], "little") + 1
    raise ValueError("unknown WebP chunk")


def jpeg_size(handle):
    read_exact(handle, 2)  # SOI
    while True:
        if read_exact(handle, 1) != b"\xff":
            raise ValueError("expected a marker")
        marker = 0xFF
        while marker == 0xFF:  # fill bytes
            marker = read_exact(handle, 1)[0]
        if marker in JPEG_STANDALONE:
            continue
        if marker in (0xD9, 0xDA):
            raise ValueError("no frame header before the image data")
        (length,) = struct.unpack(">H", read_exact(handle, 2))
        if length < 2:
            raise ValueError("bad segment length")
        if marker in JPEG_FRAMES:
            _, height, width = struct.unpack(">BHH", read_exact(handle, 5))
            return width, height
        handle.seek(length - 2, os.SEEK_CUR)


def tiff_size(handle):
    header = read_exact(handle, 8)
    order = {b"II": "<", b"MM": ">"}[header[:2]]
    (offset,) = struct.unpack(order + "I", header[4:8])
    handle.seek(offset)
    (count,) = struct.unpack(order + "H", read_exact(handle, 2))
    found = {}
    for _ in range(count):
        tag, kind, _, value = struct.unpack(order + "HHI4s", read_exact(handle, 12))
        if tag in (256, 257):  # ImageWidth, ImageLength: SHORT or LONG
            found[tag] = struct.unpack(order + {3: "H2x", 4: "I"}[kind], value)[0]
    return found[256], found[257]


def heic_size(handle):
    """The largest ispe (image spatial extents) property: the full image, not
    its tiles. It is the coded size, which HEVC pads (sips: 123x45 -> 124x46;
    clap crops it), so it errs on the large side, which is fine for a cap."""
    end = handle.seek(0, os.SEEK_END)
    sizes = []
    for _, meta_start, meta_end in boxes(handle, 0, end, b"meta"):
        for _, iprp_start, iprp_end in boxes(handle, meta_start + 4, meta_end, b"iprp"):
            for _, ipco_start, ipco_end in boxes(handle, iprp_start, iprp_end, b"ipco"):
                for _, ispe_start, _ in boxes(handle, ipco_start, ipco_end, b"ispe"):
                    handle.seek(ispe_start + 4)
                    sizes.append(struct.unpack(">II", read_exact(handle, 8)))
    return max(sizes, key=lambda size: size[0] * size[1])


def boxes(handle, start, end, wanted):
    """(kind, content start, content end) of the ISO BMFF boxes of one kind."""
    offset = start
    while offset + 8 <= end:
        handle.seek(offset)
        size, kind = struct.unpack(">I4s", read_exact(handle, 8))
        header = 8
        if size == 1:
            (size,) = struct.unpack(">Q", read_exact(handle, 8))
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise ValueError("bad box size")
        if kind == wanted:
            yield kind, offset + header, offset + size
        offset += size


READERS = {
    "png": png_size,
    "gif": gif_size,
    "bmp": bmp_size,
    "webp": webp_size,
    "jpeg": jpeg_size,
    "tiff": tiff_size,
    "heic": heic_size,
}
