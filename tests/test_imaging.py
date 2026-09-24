import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from herdr_image_viewer import imaging, png

# Fake converters: record argv, then write a solid PNG of the requested size to
# the output path (sips: --resampleHeightWidth H W ... --out OUT; magick:
# -resize WxH! ... OUT). FAKE_SLEEP makes them hang.
FAKE_TOOL = """#!/usr/bin/env python3
import json, os, sys, time, zlib, struct
arguments = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([os.path.basename(sys.argv[0])] + arguments) + "\\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
width, height = 4, 2  # plain conversions
if "--resampleHeightWidth" in arguments:
    at = arguments.index("--resampleHeightWidth")
    height, width = int(arguments[at + 1]), int(arguments[at + 2])
elif "-resize" in arguments:
    width, height = map(int, arguments[arguments.index("-resize") + 1].rstrip("!").split("x"))
out = arguments[arguments.index("--out") + 1] if "--out" in arguments else arguments[-1].split(":", 1)[-1]
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
rows = (b"\\x00" + b"\\x01\\x02\\x03" * width) * height
open(out, "wb").write(b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
"""


class FakeToolsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bin = Path(self.directory.name) / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.log = Path(self.directory.name) / "calls.jsonl"
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin), "FAKE_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.directory.cleanup()

    def install(self, *names):
        for name in names:
            path = self.bin / name
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]



def solid_png(width, height, rgb=(10, 20, 30)):
    return png.encode_rgb(width, height, bytes(rgb) * (width * height))


class PngSizeTest(unittest.TestCase):
    def test_png_dimensions_are_read_from_the_header(self):
        self.assertEqual(imaging.png_size(solid_png(7, 3)), (7, 3))

    def test_truncated_or_malformed_pngs_are_rejected(self):
        whole = solid_png(7, 3)
        for name, data in {
            "cut before IHDR ends": whole[:20],
            "cut inside the image data": whole[:-20],
            "not a PNG": b"GIF89a" + whole[6:],
            "zero width": whole[:16] + b"\x00\x00\x00\x00" + whole[20:],
        }.items():
            with self.subTest(name):
                with self.assertRaises(imaging.ImagingError):
                    imaging.png_size(data)


class ResizeTest(FakeToolsTest):
    def test_an_image_is_resized_to_an_exact_pixel_size_with_the_available_tool(self):
        self.install("magick")
        resized = imaging.resize(solid_png(40, 20), 10, 5)

        self.assertEqual(imaging.png_size(resized), (10, 5))
        (call,) = self.calls()
        self.assertEqual(call[0], "magick")
        self.assertIn("10x5!", call)
        self.assertIn("-limit", call)

    def test_sips_is_preferred_when_present(self):
        self.install("magick", "sips")
        resized = imaging.resize(solid_png(40, 20), 12, 6)

        self.assertEqual(imaging.png_size(resized), (12, 6))
        self.assertEqual([call[0] for call in self.calls()], ["sips"])

    def test_resizing_gives_up_after_its_time_limit_and_reports_an_error(self):
        self.install("magick")
        started = time.monotonic()

        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "5"}):
            with self.assertRaisesRegex(imaging.ImagingError, "timed out"):
                imaging.resize(solid_png(40, 20), 10, 5, timeout=0.5)

        self.assertLess(time.monotonic() - started, 3)


class ConvertTest(FakeToolsTest):
    def test_a_gif_or_tiff_converts_its_first_frame(self):
        self.install("magick")
        for image_format, head in (("gif", b"GIF89a"), ("tiff", b"II*\x00")):
            with self.subTest(image_format):
                converted = imaging.to_png(head + b"frames", image_format)

                self.assertEqual(imaging.png_size(converted), (4, 2))
                call = self.calls()[-1]
                inputs = [argument for argument in call if argument.endswith("[0]")]
                self.assertEqual(len(inputs), 1, call)

    def test_a_png_is_returned_as_it_is(self):
        data = solid_png(3, 3)

        self.assertIs(imaging.to_png(data, "png"), data)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
