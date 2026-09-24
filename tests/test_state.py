import unittest
from pathlib import Path

from herdr_image_viewer import state

DEFAULT = Path("/home/u/.local/state/herdr/plugins/haretoke.image-viewer")


class StoreRootTest(unittest.TestCase):
    def test_the_plugin_state_dir_is_used_only_when_it_belongs_to_this_plugin(self):
        cases = [
            ({"HERDR_PLUGIN_ID": "haretoke.image-viewer", "HERDR_PLUGIN_STATE_DIR": "/s/mine"}, Path("/s/mine")),
            ({"HERDR_PLUGIN_ID": "other.plugin", "HERDR_PLUGIN_STATE_DIR": "/s/other"}, DEFAULT),
            ({"HERDR_PLUGIN_STATE_DIR": "/s/unknown"}, DEFAULT),
            ({"HERDR_PLUGIN_ID": "haretoke.image-viewer", "HERDR_PLUGIN_STATE_DIR": "relative"}, DEFAULT),
            ({"XDG_STATE_HOME": "/xdg"}, Path("/xdg/herdr/plugins/haretoke.image-viewer")),
            ({"XDG_STATE_HOME": "relative/state"}, DEFAULT),
            ({"XDG_STATE_HOME": ""}, DEFAULT),
            ({}, DEFAULT),
        ]
        for extra, expected in cases:
            with self.subTest(environ=extra):
                self.assertEqual(state.store_root({"HOME": "/home/u", **extra}), expected)


if __name__ == "__main__":
    unittest.main()
