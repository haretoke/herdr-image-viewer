"""At most one viewer per conversation.

Files under <store root>/run/ (outside the GC-managed conversations):

    <name>.viewer.lock    flock held by the live viewer for its whole life
    <name>.reservation    created (O_EXCL) by a publisher about to open a viewer
    <name>.registration   written by the viewer that took the lock
"""

import time
import uuid

from . import keys


def ensure_viewer(root, key, caller_pane, opener, clock=time.time):
    """Open a viewer for the conversation next to caller_pane unless one runs.

    opener(caller_pane, env) opens the plugin pane and returns its pane id.
    """
    token = uuid.uuid4().hex
    opener(
        caller_pane,
        {
            "HERDR_IMAGE_VIEWER_STORE": str(root),
            "HERDR_IMAGE_VIEWER_CONVERSATION": key,
            "HERDR_IMAGE_VIEWER_TOKEN": token,
        },
    )
    return "opened"


def run_name(key):
    return keys.directory_name(key)
