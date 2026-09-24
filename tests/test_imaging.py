import unittest

from herdr_image_viewer import imaging, png


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


if __name__ == "__main__":
    unittest.main()
