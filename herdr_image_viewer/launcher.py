"""At most one viewer per conversation.

Files under <store root>/run/ (outside the GC-managed conversations):

    <name>.viewer.lock    flock held by the live viewer for its whole life
    <name>.launch.lock    flock held briefly while deciding whether to open
    <name>.reservation    written by the publisher that opens a viewer (expires)
    <name>.registration   written by the viewer that took the viewer lock
    <caller>.caller       the conversation last published from a pane (open action)
"""

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from . import keys, limits, safety, state
from .store import write_private_atomically

FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)


def run_directory(root):
    root = Path(root)
    safety.private_directory(root)
    return safety.private_directory(root / "run")


def run_paths(root, key):
    run = run_directory(root)
    name = keys.directory_name(key)
    return {
        "viewer": run / f"{name}.viewer.lock",
        "launch": run / f"{name}.launch.lock",
        "reservation": run / f"{name}.reservation",
        "registration": run / f"{name}.registration",
    }


def caller_record(root, socket_path, pane_id):
    return run_directory(root) / f"{keys.directory_name(socket_path + '#' + pane_id)}.caller"


def remember_caller(root, socket_path, pane_id, key):
    document = {"conversation": key}
    write_private_atomically(caller_record(root, socket_path, pane_id), json.dumps(document).encode("utf-8"))


def last_conversation(root, socket_path, pane_id):
    """The conversation last published from the pane, or None."""
    try:
        key = json.loads(caller_record(root, socket_path, pane_id).read_bytes())["conversation"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return key if isinstance(key, str) and keys.valid_key(key) else None


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


class OpenFailed(Exception):
    """Herdr did not open the viewer pane."""


def herdr_opener(environ, timeout=limits.OPEN_TIMEOUT_SECONDS):
    """An opener that asks Herdr for the plugin's viewer pane right of the caller.

    Every failure is an OpenFailed; the reservation stays, since a timed-out
    request may still open the pane.
    """
    herdr = environ.get("HERDR_BIN_PATH") or shutil.which("herdr", path=environ.get("PATH"))

    def open_viewer(caller_pane, env):
        if not herdr:
            raise OpenFailed("herdr is not on PATH and HERDR_BIN_PATH is not set")
        argv = [
            herdr, "plugin", "pane", "open",
            "--plugin", state.PLUGIN_ID,
            "--entrypoint", "viewer",
            "--placement", "split",
            "--target-pane", caller_pane,
            "--direction", "right",
            "--no-focus",
        ]
        for name, value in env.items():
            argv += ["--env", f"{name}={value}"]
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise OpenFailed(f"herdr did not answer within {timeout} s") from error
        except OSError as error:
            raise OpenFailed(f"cannot run {herdr}: {error.strerror}") from error
        if completed.returncode != 0:
            raise OpenFailed(completed.stderr.strip() or f"herdr exited with {completed.returncode}")
        try:
            return json.loads(completed.stdout)["result"]["plugin_pane"]["pane"]["pane_id"]
        except (ValueError, KeyError, TypeError) as error:
            raise OpenFailed(f"unexpected answer from herdr: {completed.stdout.strip()[:200]!r}") from error

    return open_viewer


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
