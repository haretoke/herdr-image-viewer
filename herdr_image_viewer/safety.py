"""Checks on untrusted input: what gets archived and what reaches the screen."""

import os
import stat

from . import limits


class UnsafeInput(Exception):
    pass


def check_size(byte_count):
    if byte_count > limits.MAX_INPUT_BYTES:
        raise UnsafeInput(
            f"image is too large ({byte_count} bytes; limit is {limits.MAX_INPUT_BYTES})"
        )


def check_pixels(width, height):
    if width <= 0 or height <= 0:
        raise UnsafeInput(f"image has no pixels ({width}x{height})")
    if width * height > limits.MAX_INPUT_PIXELS:
        raise UnsafeInput(
            f"image has too many pixels ({width}x{height}; limit is {limits.MAX_INPUT_PIXELS})"
        )


HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}


def image_format(head):
    """Name the image format from its first bytes; the file extension is ignored."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:
        return "heic"
    raise UnsafeInput("not a supported image (PNG, JPEG, GIF, WebP, BMP, TIFF, HEIC)")


def private_directory(path, uid=None):
    """Create a 0700 directory, or tighten an existing one this user owns.

    A symlink or a directory owned by someone else is refused rather than used.
    """
    uid = os.getuid() if uid is None else uid
    os.makedirs(path, mode=0o700, exist_ok=True)
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise UnsafeInput(f"storage {os.fspath(path)!r} is not a plain directory")
    if status.st_uid != uid:
        raise UnsafeInput(f"storage {os.fspath(path)!r} belongs to another user")
    if stat.S_IMODE(status.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def create_private_file(path):
    """Create a new 0600 file for writing; never follows or reuses an existing path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def open_source(path):
    """Open a regular file for reading, refusing symlinks, FIFOs, and devices.

    The caller copies from the returned handle, so the content cannot be swapped
    between the check and the copy.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise UnsafeInput(f"cannot open {os.fspath(path)!r}: {error.strerror}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafeInput(f"{os.fspath(path)!r} is not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
