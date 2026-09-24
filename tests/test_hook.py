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

    def run_hook(self, stdin, direct=False, **extra_env):
        """Through the shell wrapper, or (direct) the Python side alone, whose
        stderr the wrapper would hide."""
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
        command = ["/bin/sh", str(HOOK)]
        if direct:
            command = [sys.executable, "-m", "herdr_image_viewer", "hook", "claude-read"]
            env["PYTHONPATH"] = str(REPOSITORY)
        return subprocess.run(command, input=data, cwd=self.project, env=env, capture_output=True, timeout=60)

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

    def test_malformed_payloads_and_wrong_types_are_ignored_quietly(self):
        read = {"tool_name": "Read", "session_id": "s1"}
        cases = {
            "empty": b"",
            "not json": b"{not json",
            "not utf-8": b"\xff\xfe{}",
            "a list": b"[1, 2]",
            "tool_input is a string": {**read, "tool_input": str(self.image)},
            "file_path is a number": {**read, "tool_input": {"file_path": 42}},
            "file_path is empty": {**read, "tool_input": {"file_path": ""}},
            "file_path has a NUL": {**read, "tool_input": {"file_path": str(self.project / "a\x00b.png")}},
            "tool_name is a list": {"tool_name": ["Read"], "tool_input": {"file_path": str(self.image)}},
        }
        for label, stdin in cases.items():
            with self.subTest(label):
                result = self.run_hook(stdin, direct=True)

                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
                self.assertEqual(self.herdr_calls(), [])
                self.assertEqual(self.conversations(), [])

    def test_awkward_and_relative_paths_are_published_from_where_they_point(self):
        elsewhere = self.project / "elsewhere"
        elsewhere.mkdir()
        names = ["it's a \"quoted\" shot.png", "line\nbreak.png", "  spaced  .png", "rel.png"]
        for directory in (self.project, elsewhere):
            for name in names:
                (directory / name).write_bytes(png.encode_rgb(2, 1, bytes([len(name), directory.name == "elsewhere"]) * 3))
        (self.base / "home shot.png").write_bytes(png.encode_rgb(2, 1, bytes(6)))
        cases = [
            (self.payload(self.project / names[0]), self.project / names[0]),
            (self.payload(self.project / names[1]), self.project / names[1]),
            (self.payload(self.project / names[2]), self.project / names[2]),
            (self.payload("rel.png", cwd=str(elsewhere)), elsewhere / "rel.png"),  # the payload's cwd
            # else the hook's cwd, as getcwd reports it (symlinks resolved)
            ({**self.payload("rel.png"), "cwd": 5}, self.project.resolve() / "rel.png"),
            (self.payload("~/home shot.png"), self.base / "home shot.png"),
        ]
        for payload, expected in cases:
            with self.subTest(payload["tool_input"]["file_path"]):
                result = self.run_hook(payload)

                self.assertEqual(result.returncode, 0)
                newest = Store(self.store_root).history("claude:s1")[-1]
                self.assertEqual(newest.source, str(expected))
                self.assertEqual(Store(self.store_root).archive_path("claude:s1", newest).read_bytes(),
                                 expected.read_bytes())

    def test_a_session_id_of_the_wrong_type_falls_back_to_the_pane_key(self):
        result = self.run_hook(self.payload(self.image, session_id={"id": "s1"}))

        self.assertEqual(result.returncode, 0)
        key = f"pane:{self.base / 'herdr.sock'}#w1:p3"
        self.assertEqual([entry.name for entry in Store(self.store_root).history(key)], ["shot one.png"])


if __name__ == "__main__":
    unittest.main()
