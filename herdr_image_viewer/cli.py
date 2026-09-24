"""Command line: `viewer` runs the pane process; `publish` adds an image to a
conversation's history and makes sure a viewer shows that conversation; `open`
(the plugin action) reopens the viewer of the focused pane's last conversation;
`gc` collects the store now instead of waiting for the next hourly run."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import keys, launcher, safety, state
from .store import CapacityError, NewerSchema, Store


def main(argv, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="herdr-image-viewer")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("viewer", help="run the viewer pane (started by Herdr)")
    publish = commands.add_parser("publish", help="add an image and show it")
    publish.add_argument("image")
    publish.add_argument("--conversation", required=True)
    publish.add_argument("--caller-pane", required=True)
    commands.add_parser("gc", help="remove expired conversations and trim the store")
    commands.add_parser("open", help="reopen the viewer for the focused pane (plugin action)")
    args = parser.parse_args(argv)

    if args.command == "viewer":
        from . import app

        return app.run_from_environment(environ)
    if args.command == "gc":
        return collect(environ)
    if args.command == "open":
        return open_for_focused_pane(environ)
    return publish_image(environ, args.image, args.conversation, args.caller_pane)


def collect(environ):
    Store(state.store_root(environ)).gc()
    return 0


def publish_image(environ, image, key, caller_pane):
    if not keys.valid_key(key):
        return fail(f"invalid conversation key {key!r}")
    root = state.store_root(environ)
    try:
        Store(root).publish(key, Path(image))
    except (safety.UnsafeInput, CapacityError, NewerSchema, OSError) as error:
        return fail(error)
    launcher.remember_caller(root, environ.get("HERDR_SOCKET_PATH", ""), caller_pane, key)
    return show(environ, root, key, caller_pane)


def open_for_focused_pane(environ):
    try:
        pane = json.loads(environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}").get("focused_pane_id")
    except (ValueError, AttributeError):
        pane = None
    if not isinstance(pane, str) or not pane:
        return fail("the action context names no focused pane")
    root = state.store_root(environ)
    key = launcher.last_conversation(root, environ.get("HERDR_SOCKET_PATH", ""), pane)
    if key is None:
        return fail(f"no images were published from pane {safety.display_text(pane)}")
    return show(environ, root, key, pane)


def show(environ, root, key, caller_pane):
    try:
        status = launcher.ensure_viewer(root, key, caller_pane, launcher.herdr_opener(environ))
    except launcher.OpenFailed as error:
        return fail(f"could not open the viewer: {error}")
    print(status)
    return 0


def fail(message):
    print(f"herdr-image-viewer: {message}", file=sys.stderr)
    return 1
