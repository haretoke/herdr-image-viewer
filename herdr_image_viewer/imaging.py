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
    """PNG bytes of an image; multi-frame formats contribute their first frame."""
    if image_format == "png":
        return data
    with tempfile.TemporaryDirectory(prefix="herdr-image-viewer-") as directory:
        source = Path(directory) / f"source.{image_format}"
        target = Path(directory) / "converted.png"
        source.write_bytes(data)
        commands = {
            "sips": ["-s", "format", "png", str(source), "--out", str(target)],
            "magick": [*MAGICK_LIMITS, f"{source}[0]", f"png:{target}"],
            "convert": [*MAGICK_LIMITS, f"{source}[0]", f"png:{target}"],
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
