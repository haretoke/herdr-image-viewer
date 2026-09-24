"""hooks/claude-read.sh as Claude Code runs it: payload on stdin, fake herdr on PATH."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from herdr_image_viewer import png
from herdr_image_viewer.store import Store
from tests.test_cli import FAKE_HERDR

REPOSITORY = Path(__file__).resolve().parents[1]
HOOK = REPOSITORY / "hooks" / "claude-read.sh"


class HookTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        (self.bin / "herdr").write_text(FAKE_HERDR)
        (self.bin / "herdr").chmod(0o755)
        self.log = self.base / "herdr.log"
        self.store_root = self.base / "state" / "herdr" / "plugins" / "haretoke.image-viewer"
        self.project = self.base / "project"
        self.project.mkdir()
        self.image = self.project / "shot one.png"
        self.image.write_bytes(png.encode_rgb(4, 2, bytes(24)))

    def tearDown(self):
        self.directory.cleanup()

    def payload(self, file_path, tool_name="Read", **extra):
        return {
            "session_id": "s1",
            "cwd": str(self.project),
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": str(file_path)},
            **extra,
        }

    def run_hook(self, stdin, **extra_env):
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.base),
            "XDG_STATE_HOME": str(self.base / "state"),
            "FAKE_HERDR_LOG": str(self.log),
            "HERDR_SOCKET_PATH": str(self.base / "herdr.sock"),
            "HERDR_PANE_ID": "w1:p3",
            **extra_env,
        }
        data = stdin if isinstance(stdin, bytes) else json.dumps(stdin).encode()
        return subprocess.run(["/bin/sh", str(HOOK)], input=data, cwd=self.project, env=env,
                              capture_output=True, timeout=60)

    def herdr_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def conversations(self):
        directory = self.store_root / "conversations"
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    def test_an_image_read_is_published_and_other_reads_are_ignored(self):
        notes = self.project / "notes.txt"
        notes.write_text("not an image")

        result = self.run_hook(self.payload(self.image))

        self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)
        self.assertEqual([entry.name for entry in Store(self.store_root).history("claude:s1")], ["shot one.png"])
        self.assertEqual(len(self.herdr_calls()), 1)
        published = self.conversations()

        for label, payload in {
            "a text file": self.payload(notes),
            "an image written, not read": self.payload(self.image, tool_name="Write"),
        }.items():
            with self.subTest(label):
                result = self.run_hook(payload)

                self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)
                self.assertEqual(len(self.herdr_calls()), 1)
                self.assertEqual(self.conversations(), published)
                self.assertEqual(len(Store(self.store_root).history("claude:s1")), 1)


if __name__ == "__main__":
    unittest.main()
