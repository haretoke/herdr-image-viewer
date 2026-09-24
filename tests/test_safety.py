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


class PrivateStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.saved_umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self.saved_umask)
        self.directory.cleanup()

    def mode(self, path):
        return os.stat(path).st_mode & 0o777

    def test_storage_directories_are_created_or_tightened_to_0700(self):
        created = self.root / "state"
        existing = self.root / "loose"
        existing.mkdir(mode=0o755)

        safety.private_directory(created)
        safety.private_directory(existing)

        self.assertEqual(self.mode(created), 0o700)
        self.assertEqual(self.mode(existing), 0o700)

    def test_symlinked_or_foreign_owned_storage_is_refused(self):
        real = self.root / "real"
        real.mkdir(mode=0o700)
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(safety.UnsafeInput):
            safety.private_directory(link)
        with self.assertRaises(safety.UnsafeInput):
            safety.private_directory(real, uid=os.getuid() + 1)

    def test_files_are_created_0600_whatever_the_umask(self):
        os.umask(0)
        path = self.root / "history.json"

        with safety.create_private_file(path) as handle:
            handle.write(b"{}")

        self.assertEqual(self.mode(path), 0o600)
        with self.assertRaises(FileExistsError):
            safety.create_private_file(path)


class DisplayTextTest(unittest.TestCase):
    def test_control_characters_in_file_names_never_reach_the_title(self):
        name = "shot\x1b]0;pwned\x07\n\r\t\x7f\x9b31m‮gnp.png 画像"

        shown = safety.display_text(name)

        self.assertTrue(all(char.isprintable() for char in shown), repr(shown))
        self.assertIn("shot", shown)
        self.assertIn("画像", shown)
        self.assertNotIn("\x1b", shown)


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
