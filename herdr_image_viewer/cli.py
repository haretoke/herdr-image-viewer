"""Command line: `viewer` runs the pane process; `publish` adds an image to a
conversation's history and makes sure a viewer shows that conversation."""

import argparse
import os
from pathlib import Path

from . import launcher, state
from .store import Store


def main(argv, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="herdr-image-viewer")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("viewer", help="run the viewer pane (started by Herdr)")
    publish = commands.add_parser("publish", help="add an image and show it")
    publish.add_argument("image")
    publish.add_argument("--conversation", required=True)
    publish.add_argument("--caller-pane", required=True)
    args = parser.parse_args(argv)

    if args.command == "viewer":
        from . import app

        return app.run_from_environment(environ)
    root = state.store_root(environ)
    Store(root).publish(args.conversation, Path(args.image))
    print(launcher.ensure_viewer(root, args.conversation, args.caller_pane, launcher.herdr_opener(environ)))
    return 0
