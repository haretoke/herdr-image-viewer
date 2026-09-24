import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from herdr_image_viewer import herdr_api, limits


class FakeHerdr:
    """A Unix socket server running one scripted handler per connection."""

    def __init__(self, handler):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "herdr.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(4)
        self.handler = handler
        self.requests = []
        self.frames = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            connection, _ = self.server.accept()
        except OSError:
            return
        with connection:
            reader = connection.makefile("rb")
            request = json.loads(reader.readline())
            self.requests.append(request)
            self.handler(self, connection, reader, request)

    def reply(self, connection, request, result=None, error=None):
        body = {"id": request["id"]}
        if error is not None:
            body["error"] = error
        else:
            body["result"] = result or {"type": "ok"}
        connection.sendall((json.dumps(body) + "\n").encode())

    def read_frame(self, reader):
        line = reader.readline()
        if not line:
            return None
        header = json.loads(line)
        data = reader.read(header["data_length"])
        self.frames.append((header, len(data)))
        return header

    def close(self):
        self.server.close()
        self.thread.join(timeout=5)
        self.directory.cleanup()


class StreamOpenTest(unittest.TestCase):
    def test_opening_a_stream_waits_for_the_ok_reply(self):
        def handler(fake, connection, reader, request):
            time.sleep(0.3)
            fake.reply(connection, request)
            reader.readline()  # hold the stream open until the client closes it

        fake = FakeHerdr(handler)
        self.addCleanup(fake.close)
        stream = herdr_api.GraphicsStream(fake.path, "w1:p2", "main", 10)
        started = time.monotonic()

        stream.open()

        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(fake.requests[0]["method"], "pane.graphics.stream")
        self.assertEqual(fake.requests[0]["params"], {"pane_id": "w1:p2", "layer_id": "main", "z_index": 10})
        stream.close()

    def test_a_refused_open_raises(self):
        def handler(fake, connection, reader, request):
            fake.reply(connection, request, error={"code": "layer_limit", "message": "pane graphics layer limit reached"})

        fake = FakeHerdr(handler)
        self.addCleanup(fake.close)

        with self.assertRaisesRegex(herdr_api.HerdrError, "layer limit") as raised:
            herdr_api.GraphicsStream(fake.path, "w1:p2", "main", 10).open()
        self.assertEqual(raised.exception.code, "layer_limit")
        self.assertTrue(raised.exception.resource)


PLACEMENT = {"viewport_col": 1, "viewport_row": 2, "grid_cols": 3, "grid_rows": 1}


class StreamFrameTest(unittest.TestCase):
    def open_stream(self, handler):
        fake = FakeHerdr(handler)
        self.addCleanup(fake.close)
        stream = herdr_api.GraphicsStream(fake.path, "w1:p2", "main", 10)
        self.addCleanup(stream.close)
        stream.open()
        return fake, stream

    def test_a_rejected_frame_is_reported_and_closes_the_stream(self):
        def handler(fake, connection, reader, request):
            fake.reply(connection, request)
            fake.read_frame(reader)
            fake.reply(connection, request, error={"code": "image_too_large", "message": "frame data is too large"})

        fake, stream = self.open_stream(handler)

        with self.assertRaisesRegex(herdr_api.HerdrError, "frame data is too large"):
            stream.send(b"\x89PNG fake", 4, 2, PLACEMENT)

        self.assertFalse(stream.is_open())
        header, length = fake.frames[0]
        self.assertEqual(header, {"format": "png", "image_width": 4, "image_height": 2,
                                  "data_length": 9, "placement": PLACEMENT})
        self.assertEqual(length, 9)

    def test_an_eof_between_frames_is_noticed_without_sending_a_frame(self):
        closed = threading.Event()

        def handler(fake, connection, reader, request):
            fake.reply(connection, request)
            connection.shutdown(socket.SHUT_RDWR)  # Herdr dropped the stream (pane closed, restart)
            closed.set()

        fake, stream = self.open_stream(handler)
        self.assertTrue(closed.wait(5))
        time.sleep(0.05)

        self.assertIsNotNone(stream.lost())
        self.assertFalse(stream.is_open())
        self.assertEqual(fake.frames, [])

    def test_frames_respect_the_16_mib_limit(self):
        def handler(fake, connection, reader, request):
            fake.reply(connection, request)
            while fake.read_frame(reader):
                pass

        fake, stream = self.open_stream(handler)
        limit = limits.MAX_STREAM_FRAME_BYTES
        self.assertEqual(limit, 16 * 1024 * 1024)

        with self.assertRaisesRegex(herdr_api.HerdrError, "too large"):
            stream.send(bytes(limit + 1), 1, 1, PLACEMENT)
        stream.send(bytes(limit), 1, 1, PLACEMENT)
        stream.send(bytes(limit - 1), 1, 1, PLACEMENT)
        stream.close()
        fake.thread.join(timeout=10)

        self.assertEqual([header["data_length"] for header, _ in fake.frames], [limit, limit - 1])
        self.assertEqual([length for _, length in fake.frames], [limit, limit - 1])

    def test_a_healthy_idle_stream_is_not_lost(self):
        def handler(fake, connection, reader, request):
            fake.reply(connection, request)
            reader.readline()

        _, stream = self.open_stream(handler)

        self.assertIsNone(stream.lost())
        self.assertTrue(stream.is_open())


if __name__ == "__main__":
    unittest.main()
