import subprocess
import sys
import tempfile
import time
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

    def test_an_open_that_timed_out_is_not_retried_while_its_reservation_is_live(self):
        def timing_out(caller, env):
            self.opener.requests.append((caller, env))
            raise TimeoutError("herdr plugin pane open timed out")  # it may still open

        with self.assertRaises(TimeoutError):
            launcher.ensure_viewer(self.root, "claude:s1", "w1:p3", timing_out, clock=lambda: self.now)
        self.now += 14
        self.assertEqual(self.ensure(), "opening")
        self.assertEqual(len(self.opener.requests), 1)

        self.now += 2  # the reservation expired after 15 s
        self.assertEqual(self.ensure(), "opened")
        self.assertEqual(len(self.opener.requests), 2)

    def test_no_viewer_is_opened_while_one_is_live(self):
        viewer = launcher.claim(self.root, "claude:s1", token="live", pane_id="w1:v9")
        self.assertIsNotNone(viewer)
        self.addCleanup(viewer.release)

        self.assertEqual(self.ensure(), "running")
        self.assertEqual(self.opener.requests, [])

    def test_concurrent_publishes_and_a_manual_open_start_one_viewer(self):
        opens = Path(self.directory.name) / "opens"
        start = Path(self.directory.name) / "start"
        worker = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from herdr_image_viewer import launcher\n"
            "root, opens, start = sys.argv[1:4]\n"
            "def opener(caller, env):\n"
            "    with open(opens, 'a') as log:\n"
            "        log.write(caller + '\\n')\n"
            "    return 'w1:v1'\n"
            "while not Path(start).exists():\n"
            "    time.sleep(0.001)\n"
            "print(launcher.ensure_viewer(Path(root), 'claude:s1', 'w1:p3', opener))\n"
        )
        repository = Path(__file__).resolve().parents[1]
        workers = [
            subprocess.Popen([sys.executable, "-c", worker, str(self.root), str(opens), str(start)],
                             cwd=repository, stdout=subprocess.PIPE, text=True)
            for _ in range(12)
        ]
        time.sleep(0.5)
        start.touch()
        results = [process.communicate(timeout=30)[0].strip() for process in workers]

        self.assertEqual(opens.read_text().splitlines(), ["w1:p3"])
        self.assertEqual(sorted(results), ["opened"] + ["opening"] * 11)


if __name__ == "__main__":
    unittest.main()
