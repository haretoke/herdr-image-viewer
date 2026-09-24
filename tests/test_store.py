import hashlib
import tempfile
import unittest
from pathlib import Path

from herdr_image_viewer.store import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.now = 1_000_000.0
        self.store = Store(self.base / "state", clock=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def image(self, name, content=PNG):
        path = self.sources / name
        path.write_bytes(content)
        return path

    def test_publishing_an_image_records_it_as_the_newest_entry_with_an_archived_copy(self):
        source = self.image("shot.png")

        self.store.publish("claude:s1", source)

        history = self.store.history("claude:s1")
        self.assertEqual(len(history), 1)
        entry = history[-1]
        self.assertEqual(entry.sha256, hashlib.sha256(PNG).hexdigest())
        self.assertEqual(entry.name, "shot.png")
        self.assertEqual(entry.source, str(source))
        self.assertEqual(entry.format, "png")
        self.assertEqual(entry.published_at, self.now)
        self.assertEqual(self.store.archive_path("claude:s1", entry).read_bytes(), PNG)

    def test_publishing_the_same_content_again_moves_it_to_the_newest_position(self):
        first = self.image("first.png", PNG + b"first")
        self.store.publish("claude:s1", first)
        self.store.publish("claude:s1", self.image("second.png", PNG + b"second"))
        self.now += 60

        self.store.publish("claude:s1", self.image("first-again.png", PNG + b"first"))

        history = self.store.history("claude:s1")
        self.assertEqual([entry.name for entry in history], ["second.png", "first-again.png"])
        self.assertEqual(history[-1].published_at, self.now)

    def test_publishing_new_content_at_an_already_published_path_adds_a_new_entry(self):
        source = self.image("shot.png", PNG + b"v1")
        self.store.publish("claude:s1", source)
        source.write_bytes(PNG + b"v2")

        self.store.publish("claude:s1", source)

        history = self.store.history("claude:s1")
        self.assertEqual([entry.name for entry in history], ["shot.png", "shot.png"])
        self.assertNotEqual(history[0].sha256, history[1].sha256)
        self.assertEqual(self.store.archive_path("claude:s1", history[0]).read_bytes(), PNG + b"v1")
        self.assertEqual(self.store.archive_path("claude:s1", history[1]).read_bytes(), PNG + b"v2")


if __name__ == "__main__":
    unittest.main()
