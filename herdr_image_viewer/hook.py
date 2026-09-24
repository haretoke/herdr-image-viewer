"""The agent side: Claude Code's PostToolUse hook for the Read tool."""

import json
from pathlib import Path

from . import keys, launcher, state

# The formats safety.image_format accepts; other reads never touch the store.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


def claude_read(environ, stdin):
    """Publish the image Claude just read and make sure its viewer is open."""
    socket_path, caller_pane = environ.get("HERDR_SOCKET_PATH"), environ.get("HERDR_PANE_ID")
    if not socket_path or not caller_pane:
        return 0  # not inside Herdr
    payload = json.loads(stdin)
    if payload.get("tool_name") != "Read":
        return 0
    image = Path(payload["tool_input"]["file_path"])
    if image.suffix.lower() not in IMAGE_SUFFIXES:
        return 0
    root = state.store_root(environ)
    key = keys.conversation_key(payload, socket_path, caller_pane)
    launcher.publish_and_show(root, key, image, socket_path, caller_pane, launcher.herdr_opener(environ))
    return 0
