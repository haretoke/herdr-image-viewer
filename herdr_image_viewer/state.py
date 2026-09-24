"""Where the plugin keeps its state."""

import os
from pathlib import Path

PLUGIN_ID = "haretoke.image-viewer"


def store_root(environ):
    """The directory Herdr gives this plugin (spike 0-4).

    Herdr's own value is used inside plugin panes and actions; hooks run with
    the agent's environment and compute the same path. Relative paths are
    ignored, as the XDG base directory spec asks.
    """
    own = environ.get("HERDR_PLUGIN_STATE_DIR", "")
    if environ.get("HERDR_PLUGIN_ID") == PLUGIN_ID and os.path.isabs(own):
        return Path(own)
    base = environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(base):
        base = os.path.join(environ.get("HOME") or str(Path.home()), ".local", "state")
    return Path(base) / "herdr" / "plugins" / PLUGIN_ID
