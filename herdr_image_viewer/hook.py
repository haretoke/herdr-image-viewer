"""The agent side: Claude Code's PostToolUse hook for the Read tool."""

import contextlib
import json
import os
import signal
import traceback
from pathlib import Path

from . import keys, launcher, limits, logfile, safety, state
from .store import CapacityError, NewerSchema

# The formats safety.image_format accepts; other reads never touch the store.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


class GaveUp(Exception):
    """The hook's time budget ran out."""


# Failures with a clear cause get one log line; anything else a traceback.
EXPECTED = (safety.UnsafeInput, CapacityError, NewerSchema, OSError, GaveUp)


def claude_read(environ, stdin, budget=limits.HOOK_BUDGET_SECONDS):
    """Publish the image Claude just read and make sure its viewer is open.

    Never fails Claude's tool call: returns 0 and logs errors to hook.log
    under the store root. HERDR_IMAGE_VIEWER_HOOK=0 turns it off.
    """
    if environ.get("HERDR_IMAGE_VIEWER_HOOK", "1") == "0":
        return 0
    socket_path, caller_pane = environ.get("HERDR_SOCKET_PATH"), environ.get("HERDR_PANE_ID")
    if not socket_path or not caller_pane:
        return 0  # not inside Herdr
    try:
        payload = json.loads(stdin)
    except ValueError:  # includes undecodable bytes
        return 0
    image = image_read(payload)
    if image is None:
        return 0
    root = state.store_root(environ)
    key = keys.conversation_key(payload, socket_path, caller_pane)
    name = safety.display_text(str(image))  # file names may hold newlines or escapes
    try:
        with time_limit(budget):
            launcher.publish_and_show(root, key, image, socket_path, caller_pane, launcher.herdr_opener(environ))
    except launcher.OpenFailed as error:
        log(root, f"{name}: could not open the viewer: {error}")
    except EXPECTED as error:
        log(root, f"{name}: {error}")
    except Exception:
        log(root, f"{name}: unexpected error\n{traceback.format_exc()}")
    return 0


def image_read(payload):
    """The image file a Read payload names, or None for anything else.

    A relative path is taken from the payload's cwd (Claude's working
    directory), else from the hook's own.
    """
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return None
    tool_input = payload.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path or "\x00" in file_path:
        return None
    path = Path(os.path.expanduser(file_path))
    if not path.is_absolute():
        base = payload.get("cwd")
        path = Path(base if isinstance(base, str) and os.path.isabs(base) else os.getcwd()) / path
    return path if path.suffix.lower() in IMAGE_SUFFIXES else None


@contextlib.contextmanager
def time_limit(seconds):
    """Raise GaveUp in the main thread once seconds have passed."""
    def give_up(*_):
        raise GaveUp(f"gave up after {seconds} s")

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def log(root, text):
    logfile.append(Path(root) / "hook.log", text)
