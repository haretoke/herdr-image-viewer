import os
import tempfile
import unittest
from pathlib import Path

from herdr_image_viewer import limits, safety


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


class ImageFormatTest(unittest.TestCase):
    def test_supported_images_are_recognized_by_their_magic_bytes(self):
        samples = {
            "png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
            "gif": b"GIF89a\x01\x00\x01\x00",
            "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 ",
            "bmp": b"BM\x8a\x00\x00\x00\x00\x00",
            "tiff": b"II*\x00\x08\x00\x00\x00",
            "heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",
        }
        for expected, head in samples.items():
            with self.subTest(format=expected):
                self.assertEqual(safety.image_format(head), expected)
        self.assertEqual(safety.image_format(b"MM\x00*\x00\x00\x00\x08"), "tiff")

    def test_files_whose_magic_bytes_are_not_a_supported_image_are_rejected(self):
        for head in (b"", b"#!/bin/sh\necho", b"%PDF-1.7", b"RIFF\x24\x00\x00\x00WAVEfmt ", b"PK\x03\x04"):
            with self.subTest(head=head):
                with self.assertRaises(safety.UnsafeInput):
                    safety.image_format(head)


class CapsTest(unittest.TestCase):
    def test_files_above_the_size_cap_are_rejected(self):
        safety.check_size(limits.MAX_INPUT_BYTES)
        with self.assertRaises(safety.UnsafeInput):
            safety.check_size(limits.MAX_INPUT_BYTES + 1)

    def test_images_above_the_pixel_cap_or_without_pixels_are_rejected(self):
        safety.check_pixels(10_000, limits.MAX_INPUT_PIXELS // 10_000)
        for width, height in ((limits.MAX_INPUT_PIXELS + 1, 1), (0, 10), (10, 0)):
            with self.subTest(width=width, height=height):
                with self.assertRaises(safety.UnsafeInput):
                    safety.check_pixels(width, height)


if __name__ == "__main__":
    unittest.main()
