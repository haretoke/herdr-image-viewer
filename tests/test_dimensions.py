import io
import struct
import unittest

from herdr_image_viewer import dimensions, png


def jpeg(width, height, sof=0xC2):
    """SOI, APP0, a large APP1, fill bytes, a DHT (0xC4, not a frame), then the frame header."""
    segments = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
    segments += b"\xff\xe1" + struct.pack(">H", 60002) + bytes(60000)
    segments += b"\xff\xff\xff\xc4" + struct.pack(">H", 5) + bytes(3)
    frame = struct.pack(">BHHB", 8, height, width, 3) + bytes(9)
    return b"\xff\xd8" + segments + bytes([0xFF, sof]) + struct.pack(">H", len(frame) + 2) + frame + b"\xff\xd9"


def webp(kind, payload):
    chunk = kind + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def tiff(order, width, height):
    entries = [(256, 3, 1, struct.pack(order + "H", width) + bytes(2)),
               (257, 4, 1, struct.pack(order + "I", height))]
    ifd = struct.pack(order + "H", len(entries))
    ifd += b"".join(struct.pack(order + "HHI", tag, kind, count) + value for tag, kind, count, value in entries)
    magic = b"II*\x00" if order == "<" else b"MM\x00*"
    return magic + struct.pack(order + "I", 8) + ifd + struct.pack(order + "I", 0)


def box(kind, payload, full=False):
    body = (bytes(4) if full else b"") + payload
    return struct.pack(">I", 8 + len(body)) + kind + body


def heic(*sizes):
    """ftyp + meta (full box) > iprp > ipco > one ispe per size (e.g. tile and full image)."""
    ispes = b"".join(box(b"ispe", struct.pack(">II", width, height), full=True) for width, height in sizes)
    meta = box(b"meta", box(b"hdlr", bytes(24), full=True) + box(b"iprp", box(b"ipco", ispes)), full=True)
    return box(b"ftyp", b"heic" + bytes(4) + b"mif1") + meta + box(b"mdat", bytes(16))


class ImageSizeTest(unittest.TestCase):
    def test_dimensions_are_read_from_the_headers_of_every_supported_format(self):
        cases = {
            "png": ("png", png.encode_rgb(3, 2, bytes(18)), (3, 2)),
            "gif": ("gif", b"GIF89a" + struct.pack("<HH", 300, 200) + bytes(3), (300, 200)),
            "bmp info": ("bmp", b"BM" + bytes(12) + struct.pack("<Iii", 40, 640, -480) + bytes(16), (640, 480)),
            "bmp core": ("bmp", b"BM" + bytes(12) + struct.pack("<IHH", 12, 64, 32) + bytes(8), (64, 32)),
            "jpeg progressive": ("jpeg", jpeg(4032, 3024), (4032, 3024)),
            "jpeg baseline": ("jpeg", jpeg(17, 9, sof=0xC0), (17, 9)),
            "webp lossy": ("webp", webp(b"VP8 ", b"\x00\x00\x00\x9d\x01\x2a"
                                        + struct.pack("<HH", 1000 | 0x4000, 700 | 0x8000) + bytes(4)), (1000, 700)),
            "webp lossless": ("webp", webp(b"VP8L", b"\x2f" + struct.pack("<I", 3999 | 2999 << 14) + bytes(3)),
                              (4000, 3000)),
            "webp extended": ("webp", webp(b"VP8X", bytes(4) + (16383).to_bytes(3, "little")
                                           + (8999).to_bytes(3, "little")), (16384, 9000)),
            "tiff little-endian": ("tiff", tiff("<", 1200, 800), (1200, 800)),
            "tiff big-endian": ("tiff", tiff(">", 1200, 800), (1200, 800)),
            "heic": ("heic", heic((512, 512), (4032, 3024)), (4032, 3024)),
        }
        for label, (image_format, data, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(dimensions.image_size(io.BytesIO(data), image_format), expected)


if __name__ == "__main__":
    unittest.main()
