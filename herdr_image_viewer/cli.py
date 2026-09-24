"""Command line: `viewer` runs the pane process; `publish` adds an image to a
conversation's history and makes sure a viewer shows that conversation; `gc`
collects the store now instead of waiting for the next hourly run."""

import argparse
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
    args = parser.parse_args(argv)

    if args.command == "viewer":
        from . import app

        return app.run_from_environment(environ)
    if args.command == "gc":
        Store(state.store_root(environ)).gc()
        return 0
    if not keys.valid_key(args.conversation):
        return fail(f"invalid conversation key {args.conversation!r}")
    root = state.store_root(environ)
    try:
        Store(root).publish(args.conversation, Path(args.image))
    except (safety.UnsafeInput, CapacityError, NewerSchema, OSError) as error:
        return fail(error)
    try:
        status = launcher.ensure_viewer(root, args.conversation, args.caller_pane, launcher.herdr_opener(environ))
    except launcher.OpenFailed as error:
        return fail(f"could not open the viewer: {error}")
    print(status)
    return 0


def fail(message):
    print(f"herdr-image-viewer: {message}", file=sys.stderr)
    return 1
