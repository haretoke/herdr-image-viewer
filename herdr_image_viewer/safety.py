"""Checks on untrusted input: what gets archived and what reaches the screen."""

import os
import stat


class UnsafeInput(Exception):
    pass


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
