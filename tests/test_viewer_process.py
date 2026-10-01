"""The viewer as a process in a pseudo-terminal, against a fake Herdr socket."""

import fcntl
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path

from herdr_image_viewer import launcher, png
from herdr_image_viewer.store import Store
from tests.test_imaging import FAKE_TOOL

REPOSITORY = Path(__file__).resolve().parents[1]
PIXELS = (800, 480)  # 10x20 pixel cells on the 24x80 pty
SHOW_MAIN = b"\x1b_Ga=T,f=100,i=1,z=10,C=1,q=2"
DELETE_MAIN = b"\x1b_Ga=d,d=I,i=1,q=2\x1b\\"


class FakeHerdrServer:
    """Answers graphics info and accepts graphics streams, logging what happens."""

    def __init__(self, path):
        self.events = []
        self.lock = threading.Lock()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(8)
        self.server.settimeout(0.2)
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def log(self, *event):
        with self.lock:
            self.events.append(event)

    def serve(self):
        while self.running:
            try:
                connection, _ = self.server.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        with connection:
            reader = connection.makefile("rb")
            request = json.loads(reader.readline())
            method, params = request["method"], request.get("params", {})
            if method == "pane.graphics.info":
                result = {"type": "pane_graphics_info", "cell_width_px": 10, "cell_height_px": 20}
                connection.sendall((json.dumps({"id": request["id"], "result": result}) + "\n").encode())
                return
            if method != "pane.graphics.stream":
                connection.sendall((json.dumps({"id": request["id"], "result": {"type": "ok"}}) + "\n").encode())
                return
            layer = params["layer_id"]
            self.log("open", layer)
            connection.sendall((json.dumps({"id": request["id"], "result": {"type": "ok"}}) + "\n").encode())
            while True:
                line = reader.readline()
                if not line:
                    break
                header = json.loads(line)
                reader.read(header["data_length"])
                self.log("frame", layer)
            self.log("closed", layer)

    def wait_for(self, event, timeout=10, count=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.events.count(event) >= count:
                    return True
            time.sleep(0.05)
        return False

    def close(self):
        self.running = False
        self.thread.join(timeout=5)
        self.server.close()


class ViewerProcessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.bin = base / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        (self.bin / "magick").write_text(FAKE_TOOL)
        (self.bin / "magick").chmod(0o755)
        self.store_root = base / "state"
        source = base / "shot.png"
        source.write_bytes(png.encode_rgb(40, 20, b"\x10\x20\x30" * 800))
        Store(self.store_root).publish("claude:s1", source)
        self.socket_path = str(base / "herdr.sock")
        self.herdr = FakeHerdrServer(self.socket_path)
        self.addCleanup(self.herdr.close)
        self.output = b""

    def tearDown(self):
        self.directory.cleanup()

    def start(self, pixels=(0, 0), **env_changes):
        """The viewer on the slave side of a 24x80 pty with the given pixel size.
        subprocess instead of pty.fork: forking a process that runs the fake
        server's threads is unsafe. env_changes set variables, or remove them
        when None."""
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, *pixels))
        env = {
            "PATH": str(self.bin),
            "FAKE_LOG": os.devnull,
            "HERDR_SOCKET_PATH": self.socket_path,
            "HERDR_PANE_ID": "w1:p9",
            "HERDR_IMAGE_VIEWER_STORE": str(self.store_root),
            "HERDR_IMAGE_VIEWER_CONVERSATION": "claude:s1",
        }
        env.update(env_changes)
        env = {name: value for name, value in env.items() if value is not None}
        process = subprocess.Popen(
            [sys.executable, "-m", "herdr_image_viewer", "viewer"],
            stdin=slave, stdout=slave, stderr=slave, cwd=REPOSITORY, env=env, start_new_session=True,
        )
        os.close(slave)
        self.addCleanup(self.kill, process)
        return process, master

    def kill(self, process):
        process.kill()
        process.wait()

    def drain(self, master, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                self.output += chunk

    def wait_output(self, master, needle, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output:
                return True
            self.drain(master, 0.05)
        return False

    def wait_exit(self, process, master, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain(master, 0.05)
            if process.poll() is not None:
                return process.returncode
        self.fail("the viewer did not exit")

    def test_the_viewer_draws_kitty_images_in_its_own_pane_and_deletes_them_on_exit(self):
        process, master = self.start(pixels=PIXELS)
        self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])

        os.write(master, b"q")

        self.assertEqual(self.wait_exit(process, master), 0)
        deleted_main = self.output.find(DELETE_MAIN)
        deleted_thumbs = self.output.find(b"\x1b_Ga=d,d=I,i=2,q=2\x1b\\")
        left_alternate_screen = self.output.find(b"\x1b[?1049l")
        self.assertGreater(deleted_main, 0)
        self.assertGreater(deleted_thumbs, 0)
        self.assertLess(max(deleted_main, deleted_thumbs), left_alternate_screen)  # still on its screen
        self.assertEqual(self.herdr.events, [])  # nothing goes through the socket

    def test_without_a_pixel_size_the_viewer_waits_for_herdr_and_draws_once_a_resize_brings_one(self):
        process, master = self.start()  # no client attached: the pty has no pixel size

        self.assertTrue(self.wait_output(master, b"waiting for Herdr: the cell size is unknown"),
                        self.output[-300:])
        self.assertNotIn(SHOW_MAIN, self.output)

        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, *PIXELS))
        process.send_signal(signal.SIGWINCH)  # a client attached and resized the pane

        self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])
        os.write(master, b"q")
        self.assertEqual(self.wait_exit(process, master), 0)

    def test_q_exits_and_restores_the_terminal(self):
        process, master = self.start(pixels=PIXELS)
        self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])
        self.drain(master, 0.3)

        os.write(master, b"q")

        self.assertEqual(self.wait_exit(process, master), 0)
        self.assertIn(b"\x1b[?1049h", self.output)  # alternate screen entered
        self.assertIn(b"\x1b[?1049l", self.output)  # and left
        self.assertIn(b"\x1b[?25h", self.output)  # cursor shown again
        self.assertIn(b"1/1 shot.png 40x20", self.output)

    def test_x_removes_the_image_from_the_store_and_the_next_publish_shows_again(self):
        store = Store(self.store_root)
        shot = store.history("claude:s1")[0]
        process, master = self.start(pixels=PIXELS)
        self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])
        self.drain(master, 0.3)

        os.write(master, b"x")

        self.assertTrue(self.wait_output(master, DELETE_MAIN), self.output[-300:])
        self.assertTrue(self.wait_output(master, b"no images yet"), self.output[-300:])
        self.assertEqual(store.history("claude:s1"), [])
        self.assertFalse(store.archive_path("claude:s1", shot).exists())

        source = Path(self.directory.name) / "next.png"
        source.write_bytes(png.encode_rgb(40, 20, b"\x30\x20\x10" * 800))
        store.publish("claude:s1", source)

        self.assertTrue(self.wait_output(master, b"1/1 next.png 40x20"), self.output[-300:])
        self.assertEqual(self.output.count(SHOW_MAIN), 2)
        os.write(master, b"q")
        self.assertEqual(self.wait_exit(process, master), 0)
        self.assertIn(b"1/1 next.png 40x20", self.output)
        self.assertFalse((self.store_root / "viewer.log").exists())

    def test_sigterm_and_sighup_exit_and_restore_the_terminal(self):
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                self.herdr.events.clear()
                self.output = b""
                process, master = self.start(pixels=PIXELS)
                self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])
                self.drain(master, 0.3)

                process.send_signal(signum)

                self.assertEqual(self.wait_exit(process, master), 0)
                self.assertIn(DELETE_MAIN, self.output)
                self.assertIn(b"\x1b[?1049l", self.output)
                os.close(master)

    def test_an_unexpected_error_is_logged_under_the_state_directory_before_exiting(self):
        history = Store(self.store_root).history_path("claude:s1")
        history.chmod(0)  # reading the history raises PermissionError
        process, master = self.start()

        self.assertEqual(self.wait_exit(process, master), 1)

        log = (self.store_root / "viewer.log").read_text()
        self.assertIn("PermissionError", log)
        self.assertIn("Traceback", log)
        self.assertIn(b"\x1b[?1049l", self.output)  # the terminal was still restored
        self.assertEqual((self.store_root / "viewer.log").stat().st_mode & 0o777, 0o600)

    def test_a_viewer_exits_before_drawing_when_another_is_live(self):
        live = launcher.claim(self.store_root, "claude:s1", token="live", pane_id="w1:p8")
        self.addCleanup(live.release)
        process, master = self.start()

        self.assertEqual(self.wait_exit(process, master), 0)
        self.assertEqual(self.herdr.events, [])
        self.assertNotIn(b"\x1b[?1049h", self.output)

    def test_a_viewer_opened_without_publish_env_explains_itself_and_waits_for_q(self):
        process, master = self.start(
            HERDR_IMAGE_VIEWER_STORE=None,
            HERDR_IMAGE_VIEWER_CONVERSATION=None,
            HERDR_PLUGIN_ID="haretoke.image-viewer",
            HERDR_PLUGIN_STATE_DIR=str(self.store_root),
        )
        self.drain(master, 1.0)
        self.assertIsNone(process.poll())  # still open, so the message can be read
        self.assertIn(b"use the Open image viewer action", b" ".join(self.output.split()))  # wrapped

        os.write(master, b"q")

        self.assertEqual(self.wait_exit(process, master), 0)
        self.assertEqual(self.herdr.events, [])
        self.assertIn(b"\x1b[?1049l", self.output)
        self.assertFalse((self.store_root / "viewer.log").exists())

    def test_the_pane_going_away_ends_the_viewer(self):
        process, master = self.start(pixels=PIXELS)
        self.assertTrue(self.wait_output(master, SHOW_MAIN), self.output[-300:])
        self.drain(master, 0.3)

        os.close(master)  # EOF / EIO on the viewer's terminal

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("the viewer did not exit")


if __name__ == "__main__":
    unittest.main()
