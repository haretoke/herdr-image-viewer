"""The command line as a process, with a fake `herdr` on PATH."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from herdr_image_viewer import png
from herdr_image_viewer.store import Store

REPOSITORY = Path(__file__).resolve().parents[1]

FAKE_HERDR = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_HERDR_LOG"], "a") as log:
    log.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
print(json.dumps({"id": "cli", "result": {"type": "plugin_pane_opened",
                  "plugin_pane": {"pane": {"pane_id": "w1:v1"}}}}))
"""


def options(argv):
    """`--name value` pairs of a command line; repeated --env values collected."""
    found, envs = {}, {}
    rest = iter(argv)
    for argument in rest:
        if argument == "--no-focus":
            found["no-focus"] = True
        elif argument == "--env":
            name, _, value = next(rest).partition("=")
            envs[name] = value
        elif argument.startswith("--"):
            found[argument[2:]] = next(rest)
    return found, envs


class CliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.write_tool(self.bin / "herdr", FAKE_HERDR)
        self.log = self.base / "herdr.log"
        self.store_root = self.base / "state" / "herdr" / "plugins" / "haretoke.image-viewer"
        self.image = self.base / "shot one.png"
        self.image.write_bytes(png.encode_rgb(4, 2, b"\x10\x20\x30" * 8))

    def tearDown(self):
        self.directory.cleanup()

    def write_tool(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_cli(self, *argv, **extra_env):
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.base),
            "XDG_STATE_HOME": str(self.base / "state"),
            "FAKE_HERDR_LOG": str(self.log),
            **extra_env,
        }
        return subprocess.run([sys.executable, "-m", "herdr_image_viewer", *argv], cwd=REPOSITORY,
                              env=env, capture_output=True, text=True, timeout=30)

    def herdr_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_publish_stores_the_image_and_opens_a_viewer_right_of_the_caller_without_focus(self):
        result = self.run_cli("publish", str(self.image), "--conversation", "claude:s1", "--caller-pane", "w1:p3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([entry.name for entry in Store(self.store_root).history("claude:s1")], ["shot one.png"])
        ((program, *argv),) = self.herdr_calls()
        self.assertEqual((program, argv[:3]), ("herdr", ["plugin", "pane", "open"]))
        found, envs = options(argv[3:])
        self.assertEqual(found, {
            "plugin": "haretoke.image-viewer",
            "entrypoint": "viewer",
            "placement": "split",
            "target-pane": "w1:p3",
            "direction": "right",
            "no-focus": True,
        })
        self.assertEqual(envs["HERDR_IMAGE_VIEWER_STORE"], str(self.store_root))
        self.assertEqual(envs["HERDR_IMAGE_VIEWER_CONVERSATION"], "claude:s1")
        self.assertRegex(envs["HERDR_IMAGE_VIEWER_TOKEN"], r"^[0-9a-f]{32}$")

    def fill_store_with_a_protected_conversation(self):
        """Over 500 MiB that GC must keep: a newer-schema history (sparse file)."""
        protected = self.store_root / "conversations" / ("0" * 32)
        (protected / "archive").mkdir(parents=True)
        (protected / "history.json").write_text(json.dumps({"schema_version": 99, "entries": []}))
        with open(protected / "archive" / "big.png", "wb") as handle:
            handle.truncate(501 * 1024 * 1024)

    def test_publish_refuses_an_unsafe_file_an_invalid_key_and_a_full_store_without_opening(self):
        notes = self.base / "notes.txt"
        notes.write_text("not an image")
        cases = {
            "unsafe file": (str(notes), "claude:s1", None, "not a supported image"),
            "missing file": (str(self.base / "gone.png"), "claude:s1", None, "cannot open"),
            "unknown namespace": (str(self.image), "bogus:s1", None, "invalid conversation key"),
            "no namespace": (str(self.image), "s1", None, "invalid conversation key"),
            "control character": (str(self.image), "claude:s\n1", None, "invalid conversation key"),
            "full store": (str(self.image), "claude:s1", self.fill_store_with_a_protected_conversation,
                           "image store is full"),
        }
        for label, (image, key, prepare, reason) in cases.items():
            with self.subTest(label):
                if prepare:
                    prepare()
                result = self.run_cli("publish", image, "--conversation", key, "--caller-pane", "w1:p3")

                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertRegex(result.stderr, r"^herdr-image-viewer: \S")
                self.assertIn(reason, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.herdr_calls(), [])

    def test_publish_exits_1_when_herdr_cannot_open_the_viewer_but_keeps_the_image(self):
        self.write_tool(self.bin / "herdr", "#!/bin/sh\necho 'error: pane w1:p3 not found' >&2\nexit 3\n")

        result = self.run_cli("publish", str(self.image), "--conversation", "claude:s1", "--caller-pane", "w1:p3")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("could not open the viewer", result.stderr)
        self.assertIn("pane w1:p3 not found", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(Store(self.store_root).history("claude:s1")), 1)

    def test_publish_prefers_the_herdr_binary_named_by_herdr_bin_path(self):
        chosen = self.base / "herdr-of-this-server"
        self.write_tool(chosen, FAKE_HERDR)

        result = self.run_cli("publish", str(self.image), "--conversation", "claude:s1", "--caller-pane", "w1:p3",
                              HERDR_BIN_PATH=str(chosen))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in self.herdr_calls()], ["herdr-of-this-server"])


    def test_gc_collects_expired_conversations(self):
        fifteen_days_ago = time.time() - 15 * 24 * 3600
        Store(self.store_root).publish("claude:new", self.image)
        # Published last, so its own periodic GC ran at that old time.
        Store(self.store_root, clock=lambda: fifteen_days_ago).publish("claude:old", self.image)
        self.assertEqual(len(Store(self.store_root).history("claude:old")), 1)

        result = self.run_cli("gc")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Store(self.store_root).history("claude:old"), [])
        self.assertEqual(len(Store(self.store_root).history("claude:new")), 1)

if __name__ == "__main__":
    unittest.main()
