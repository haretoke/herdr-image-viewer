"""Private log files under the store root, for processes whose output nobody sees."""

import os
import time

from . import safety


def append(path, text):
    """Append a timestamped entry to a 0600 log file; never raises."""
    try:
        safety.private_directory(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text.rstrip()}\n")
    except (OSError, safety.UnsafeInput):
        pass  # nowhere left to report it
