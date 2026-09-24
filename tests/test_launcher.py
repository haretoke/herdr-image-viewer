import tempfile
import unittest
from pathlib import Path

from herdr_image_viewer import launcher


class FakeOpener:
    """Stands in for `herdr plugin pane open`: records each request."""

    def __init__(self):
        self.requests = []

    def __call__(self, caller_pane, env):
        self.requests.append((caller_pane, env))
        return f"w1:v{len(self.requests)}"


class EnsureViewerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "state"
        self.now = 1000.0
        self.opener = FakeOpener()

    def tearDown(self):
        self.directory.cleanup()

    def ensure(self, caller="w1:p3"):
        return launcher.ensure_viewer(self.root, "claude:s1", caller, self.opener, clock=lambda: self.now)

    def test_publish_opens_the_viewer_next_to_the_caller_when_the_conversation_has_none(self):
        self.assertEqual(self.ensure(), "opened")

        ((caller, env),) = self.opener.requests
        self.assertEqual(caller, "w1:p3")
        self.assertEqual(env["HERDR_IMAGE_VIEWER_STORE"], str(self.root))
        self.assertEqual(env["HERDR_IMAGE_VIEWER_CONVERSATION"], "claude:s1")
        self.assertRegex(env["HERDR_IMAGE_VIEWER_TOKEN"], r"^[0-9a-f]{32}$")


if __name__ == "__main__":
    unittest.main()
