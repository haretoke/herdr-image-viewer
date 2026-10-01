import unittest

from herdr_image_viewer import kitty


class TransmitTest(unittest.TestCase):
    def test_a_png_is_placed_at_its_cell_as_one_kitty_escape(self):
        sequence = kitty.transmit(image_id=1, z_index=10, col=4, row=2, data=b"PNGDATA")

        self.assertEqual(sequence, "\x1b[3;5H\x1b_Ga=T,f=100,i=1,z=10,C=1,q=2;UE5HREFUQQ==\x1b\\")


if __name__ == "__main__":
    unittest.main()
