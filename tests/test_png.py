import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from herdr_image_viewer import png


def chunks(data):
    """(type, payload, crc_ok) for every chunk after the signature."""
    found = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack_from(">I", data, offset)
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack_from(">I", data, offset + 8 + length)
        found.append((kind, payload, crc == zlib.crc32(kind + payload) & 0xFFFFFFFF))
        offset += 12 + length
    return found


class EncodeTest(unittest.TestCase):
    RGB = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255,   # red, green, blue
                 10, 20, 30, 40, 50, 60, 70, 80, 90])

    def test_the_encoder_writes_valid_chunks_and_crcs(self):
        data = png.encode_rgb(3, 2, self.RGB)

        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        found = chunks(data)
        self.assertEqual([kind for kind, _, _ in found], [b"IHDR", b"IDAT", b"IEND"])
        self.assertTrue(all(ok for _, _, ok in found))
        self.assertEqual(found[0][1], struct.pack(">IIBBBBB", 3, 2, 8, 2, 0, 0, 0))
        rows = zlib.decompress(found[1][1])
        self.assertEqual(rows, b"\x00" + self.RGB[:9] + b"\x00" + self.RGB[9:])

    def test_an_external_decoder_reads_it_back(self):
        tool = shutil.which("sips") or shutil.which("magick")
        if tool is None:
            self.skipTest("no sips or ImageMagick to decode with")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.png"
            path.write_bytes(png.encode_rgb(3, 2, self.RGB))
            if tool.endswith("sips"):
                output = subprocess.run([tool, "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                                        capture_output=True, text=True, check=True).stdout
                self.assertIn("pixelWidth: 3", output)
                self.assertIn("pixelHeight: 2", output)
            else:
                output = subprocess.run([tool, str(path), "-depth", "8", "RGB:-"],
                                        capture_output=True, check=True).stdout
                self.assertEqual(output, self.RGB)


if __name__ == "__main__":
    unittest.main()
