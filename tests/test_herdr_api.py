import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from herdr_image_viewer import herdr_api


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

        with self.assertRaisesRegex(herdr_api.HerdrError, "layer limit"):
            herdr_api.GraphicsStream(fake.path, "w1:p2", "main", 10).open()


if __name__ == "__main__":
    unittest.main()
