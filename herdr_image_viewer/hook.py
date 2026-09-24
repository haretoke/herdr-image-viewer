"""The agent side: Claude Code's PostToolUse hook for the Read tool."""

import json
import os
from pathlib import Path

from . import keys, launcher, state

# The formats safety.image_format accepts; other reads never touch the store.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


def claude_read(environ, stdin):
    """Publish the image Claude just read and make sure its viewer is open."""
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
    launcher.publish_and_show(root, key, image, socket_path, caller_pane, launcher.herdr_opener(environ))
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
