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


def run_first_available(commands, target, timeout):
    """Try the tools found on PATH in the order given; return target's bytes
    from the first that succeeds."""
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
            return target.read_bytes()
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
