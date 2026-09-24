"""Checks on untrusted input: what gets archived and what reaches the screen."""

import os
import stat


class UnsafeInput(Exception):
    pass


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
