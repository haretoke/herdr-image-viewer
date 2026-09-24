"""Image conversion and resizing through the platform's tools.

macOS has sips, Linux containers ImageMagick; ffmpeg is used when present.
Herdr draws pixels 1:1, so every image is resized to its exact rendered size.
"""

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from . import limits, png

IEND = png.chunk(b"IEND", b"")
# ImageMagick resource limits (its security policy's knobs), applied per call.
MAGICK_LIMITS = [
    "-limit", "memory", "256MiB",
    "-limit", "map", "512MiB",
    "-limit", "disk", "1GiB",
    "-limit", "time", str(limits.CONVERT_TIMEOUT_SECONDS),
]


class ImagingError(Exception):
    pass


# sips has no auto-orient: EXIF orientation -> rotate (clockwise) and flip steps.
SIPS_ORIENTATION = {
    2: ["-f", "horizontal"],
    3: ["-r", "180"],
    4: ["-f", "vertical"],
    5: ["-r", "90", "-f", "horizontal"],
    6: ["-r", "90"],
    7: ["-r", "270", "-f", "horizontal"],
    8: ["-r", "270"],
}


def exif_orientation(data):
    """The EXIF Orientation (1-8) of a JPEG, or 1 when it has none."""
    try:
        if not data.startswith(b"\xff\xd8"):
            return 1
        offset = 2
        while offset + 4 <= len(data) and data[offset] == 0xFF:
            marker = data[offset + 1]
            if marker in (0xD9, 0xDA):  # end of image, start of scan
                return 1
            (length,) = struct.unpack(">H", data[offset + 2:offset + 4])
            segment = data[offset + 4:offset + 2 + length]
            if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
                return tiff_orientation(segment[6:])
            offset += 2 + length
    except struct.error:
        pass
    return 1


def tiff_orientation(tiff):
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return 1
    (ifd,) = struct.unpack(order + "I", tiff[4:8])
    (count,) = struct.unpack(order + "H", tiff[ifd:ifd + 2])
    for index in range(count):
        entry = tiff[ifd + 2 + index * 12:ifd + 14 + index * 12]
        tag, _, _, value = struct.unpack(order + "HHI4s", entry)
        if tag == 0x0112:
            (orientation,) = struct.unpack(order + "H", value[:2])
            return orientation if 1 <= orientation <= 8 else 1
    return 1


def resize(data, width, height, timeout=limits.CONVERT_TIMEOUT_SECONDS):
    """PNG bytes of the image resized to exactly width x height pixels."""
    with tempfile.TemporaryDirectory(prefix="herdr-image-viewer-") as directory:
        source = Path(directory) / "source.png"
        target = Path(directory) / "resized.png"
        source.write_bytes(data)
        commands = {
            "sips": ["-s", "format", "png", "--resampleHeightWidth", str(height), str(width),
                     str(source), "--out", str(target)],
            "magick": [*MAGICK_LIMITS, str(source), "-resize", f"{width}x{height}!", f"png:{target}"],
            "convert": [*MAGICK_LIMITS, str(source), "-resize", f"{width}x{height}!", f"png:{target}"],
            "ffmpeg": ["-v", "error", "-y", "-i", str(source), "-vf", f"scale={width}:{height}",
                       "-frames:v", "1", str(target)],
        }
        result = run_first_available(commands, target, timeout)
    if png_size(result) != (width, height):
        raise ImagingError(f"resizing produced {png_size(result)}, not {width}x{height}")
    return result


def to_png(data, image_format, timeout=limits.CONVERT_TIMEOUT_SECONDS):
    """PNG bytes of an image, upright per its EXIF orientation (not with
    ffmpeg); multi-frame formats contribute their first frame."""
    if image_format == "png":
        return data
    sips_orientation = SIPS_ORIENTATION.get(exif_orientation(data), [])
    with tempfile.TemporaryDirectory(prefix="herdr-image-viewer-") as directory:
        source = Path(directory) / f"source.{image_format}"
        target = Path(directory) / "converted.png"
        source.write_bytes(data)
        commands = {
            "sips": [*sips_orientation, "-s", "format", "png", str(source), "--out", str(target)],
            "magick": [*MAGICK_LIMITS, f"{source}[0]", "-auto-orient", f"png:{target}"],
            "convert": [*MAGICK_LIMITS, f"{source}[0]", "-auto-orient", f"png:{target}"],
            "ffmpeg": ["-v", "error", "-y", "-i", str(source), "-frames:v", "1", str(target)],
        }
        result = run_first_available(commands, target, timeout)
    png_size(result)
    return result


BGRA_MASKS = (0xFF0000, 0xFF00, 0xFF, 0xFF000000)


def thumbnail_rgba(data, width, height, timeout=limits.CONVERT_TIMEOUT_SECONDS):
    """Straight RGBA pixels (top row first) of a PNG resized to width x height.

    sips writes a 32 bpp BGRA bitfield BMP; ImageMagick and ffmpeg write raw
    RGBA. Output of the wrong length is refused.
    """
    with tempfile.TemporaryDirectory(prefix="herdr-image-viewer-") as directory:
        source = Path(directory) / "source.png"
        target = Path(directory) / "thumbnail.out"
        source.write_bytes(data)
        size = f"{width}x{height}!"
        commands = {
            "sips": ["-s", "format", "bmp", "--resampleHeightWidth", str(height), str(width),
                     str(source), "--out", str(target)],
            "magick": [*MAGICK_LIMITS, str(source), "-resize", size, "-depth", "8", f"RGBA:{target}"],
            "convert": [*MAGICK_LIMITS, str(source), "-resize", size, "-depth", "8", f"RGBA:{target}"],
            "ffmpeg": ["-v", "error", "-y", "-i", str(source), "-vf", f"scale={width}:{height}",
                       "-f", "rawvideo", "-pix_fmt", "rgba", str(target)],
        }
        tool, output = run_first_available(commands, target, timeout, with_tool=True)
    if tool == "sips":
        bmp_width, bmp_height, output = bmp_rgba(output)
        if (bmp_width, bmp_height) != (width, height):
            raise ImagingError(f"thumbnail is {bmp_width}x{bmp_height}, not {width}x{height}")
    if len(output) != width * height * 4:
        raise ImagingError(f"thumbnail has {len(output)} bytes, not {width * height * 4}")
    return output


def bmp_rgba(data):
    """(width, height, RGBA top row first) of the BMPs sips writes: 24 bpp BI_RGB
    for opaque images, 32 bpp BGRA bitfields for images with alpha."""
    try:
        if data[:2] != b"BM":
            raise ImagingError("not a BMP")
        (offset,) = struct.unpack_from("<I", data, 10)
        _, width, height, _, bits, compression = struct.unpack_from("<IiiHHI", data, 14)
        masks = struct.unpack_from("<IIII", data, 54) if compression == 3 else None
    except struct.error as error:
        raise ImagingError("truncated BMP") from error
    supported = (bits == 24 and compression == 0) or (bits == 32 and masks == BGRA_MASKS)
    if not supported or width <= 0 or height == 0:
        raise ImagingError("unsupported BMP layout")
    rows, pixel_bytes = abs(height), bits // 8
    row_bytes = width * pixel_bytes
    stride = (row_bytes + 3) & ~3  # rows are padded to 4 bytes
    if len(data) < offset + rows * stride:
        raise ImagingError("truncated BMP pixels")
    order = range(rows) if height < 0 else range(rows - 1, -1, -1)  # positive height: bottom-up
    pixels = b"".join(data[offset + row * stride:offset + row * stride + row_bytes] for row in order)
    rgba = bytearray(width * rows * 4)
    rgba[0::4] = pixels[2::pixel_bytes]
    rgba[1::4] = pixels[1::pixel_bytes]
    rgba[2::4] = pixels[0::pixel_bytes]
    rgba[3::4] = pixels[3::4] if pixel_bytes == 4 else b"\xff" * (width * rows)
    return width, rows, bytes(rgba)


def run_first_available(commands, target, timeout, with_tool=False):
    """Try the tools found on PATH in the order given; return target's bytes
    from the first that succeeds (with the tool's name if with_tool)."""
    errors = []
    for tool, arguments in commands.items():
        executable = shutil.which(tool)
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [executable, *arguments],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{tool}: timed out after {timeout} s")
            continue
        if completed.returncode == 0 and target.is_file():
            return (tool, target.read_bytes()) if with_tool else target.read_bytes()
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        errors.append(f"{tool}: {detail[-1] if detail else 'failed'}")
    if not errors:
        raise ImagingError("no image converter found (sips, ImageMagick, or ffmpeg)")
    raise ImagingError("; ".join(errors))


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
