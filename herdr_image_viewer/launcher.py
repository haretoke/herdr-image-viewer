"""At most one viewer per conversation.

Files under <store root>/run/ (outside the GC-managed conversations):

    <name>.viewer.lock    flock held by the live viewer for its whole life
    <name>.launch.lock    flock held briefly while deciding whether to open
    <name>.reservation    written by the publisher that opens a viewer (expires)
    <name>.registration   written by the viewer that took the viewer lock
"""

import contextlib
import fcntl
import json
import os
import time
import uuid
from pathlib import Path

from . import keys, limits, safety
from .store import write_private_atomically

FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)


def run_paths(root, key):
    root = Path(root)
    run = root / "run"
    safety.private_directory(root)
    safety.private_directory(run)
    name = keys.directory_name(key)
    return {
        "viewer": run / f"{name}.viewer.lock",
        "launch": run / f"{name}.launch.lock",
        "reservation": run / f"{name}.reservation",
        "registration": run / f"{name}.registration",
    }


def ensure_viewer(root, key, caller_pane, opener, clock=time.time):
    """Open a viewer for the conversation next to caller_pane unless one runs
    ("running") or another publisher is opening one ("opening").

    opener(caller_pane, env) opens the plugin pane and returns its pane id.
    """
    paths = run_paths(root, key)
    token = uuid.uuid4().hex
    with locked(paths["launch"]):
        if viewer_is_live(paths["viewer"]):
            return "running"
        if reservation_is_live(paths["reservation"], clock):
            return "opening"
        document = {"token": token, "expires_at": clock() + limits.OPEN_RESERVATION_SECONDS}
        write_private_atomically(paths["reservation"], json.dumps(document).encode("utf-8"))
    opener(
        caller_pane,
        {
            "HERDR_IMAGE_VIEWER_STORE": str(root),
            "HERDR_IMAGE_VIEWER_CONVERSATION": key,
            "HERDR_IMAGE_VIEWER_TOKEN": token,
        },
    )
    return "opened"


@contextlib.contextmanager
def locked(path):
    descriptor = os.open(path, FLAGS, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def viewer_is_live(path):
    descriptor = os.open(path, FLAGS, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)  # also releases a lock taken just now
    return False


def reservation_is_live(path, clock):
    try:
        document = json.loads(path.read_bytes())
        return clock() < float(document["expires_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Claim:
    """The live viewer's hold on its conversation."""

    def __init__(self, descriptor, registration, token):
        self.descriptor = descriptor
        self.registration = registration
        self.token = token

    def release(self):
        remove_if_token(self.registration, self.token)
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def claim(root, key, token, pane_id):
    """Take the viewer lock and register, or return None if another viewer is live."""
    paths = run_paths(root, key)
    descriptor = os.open(paths["viewer"], FLAGS, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    document = {"token": token, "pane_id": pane_id, "pid": os.getpid()}
    write_private_atomically(paths["registration"], json.dumps(document).encode("utf-8"))
    # The open this viewer came from is done. Holding the viewer lock keeps
    # publishers from writing a new reservation between the read and the unlink.
    remove_if_token(paths["reservation"], token)
    return Claim(descriptor, paths["registration"], token)


def remove_if_token(path, token):
    try:
        if json.loads(path.read_bytes()).get("token") == token:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass
