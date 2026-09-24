import os
import tempfile
import unittest
from pathlib import Path

from herdr_image_viewer import safety


class OpenSourceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image = self.root / "shot.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n image bytes")

    def tearDown(self):
        self.directory.cleanup()

    def test_a_regular_file_is_opened_and_read_from_its_descriptor(self):
        with safety.open_source(self.image) as handle:
            self.image.unlink()  # the opened descriptor still reads the content
            self.assertEqual(handle.read(), b"\x89PNG\r\n\x1a\n image bytes")

    def test_symlinks_fifos_and_directories_are_rejected(self):
        link = self.root / "link.png"
        link.symlink_to(self.image)
        fifo = self.root / "pipe.png"
        os.mkfifo(fifo)
        directory = self.root / "folder.png"
        directory.mkdir()
        for path in (link, fifo, directory, self.root / "missing.png"):
            with self.subTest(path=path.name):
                with self.assertRaises(safety.UnsafeInput):
                    safety.open_source(path)


if __name__ == "__main__":
    unittest.main()
