"""Command line: `viewer` runs the pane process."""

import os


def main(argv):
    if argv[:1] == ["viewer"]:
        from . import app

        return app.run_from_environment(os.environ)
    print("usage: herdr-image-viewer viewer", flush=True)
    return 2
