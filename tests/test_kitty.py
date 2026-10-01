import base64
import fcntl
import os
import struct
import termios
import unittest

from herdr_image_viewer import kitty


class TransmitTest(unittest.TestCase):
    def test_a_png_is_placed_at_its_cell_as_one_kitty_escape(self):
        sequence = kitty.transmit(image_id=1, z_index=10, col=4, row=2, data=b"PNGDATA")

        self.assertEqual(sequence, "\x1b[3;5H\x1b_Ga=T,f=100,i=1,z=10,C=1,q=2;UE5HREFUQQ==\x1b\\")

    def test_data_longer_than_one_chunk_is_split_into_4096_byte_base64_chunks(self):
        data = bytes(range(256)) * 20  # 6828 bytes of base64
        encoded = base64.standard_b64encode(data).decode("ascii")

        sequence = kitty.transmit(image_id=2, z_index=20, col=0, row=0, data=data)

        self.assertEqual(sequence, "\x1b[1;1H"
                                   f"\x1b_Ga=T,f=100,i=2,z=20,C=1,q=2,m=1;{encoded[:4096]}\x1b\\"
                                   f"\x1b_Gm=0;{encoded[4096:]}\x1b\\")


class DeleteTest(unittest.TestCase):
    def test_deleting_an_image_frees_it_by_id(self):
        self.assertEqual(kitty.delete(image_id=2), "\x1b_Ga=d,d=I,i=2,q=2\x1b\\")


class CellSizeTest(unittest.TestCase):
    def pty(self, rows, cols, width_px, height_px):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, width_px, height_px))
        return slave

    def test_the_cell_size_is_the_pty_pixel_size_divided_by_its_cells(self):
        self.assertEqual(kitty.cell_size(self.pty(24, 80, 800, 480)), (10, 20))

    def test_the_cell_size_is_unknown_while_the_pixel_size_is_zero(self):
        self.assertIsNone(kitty.cell_size(self.pty(24, 80, 0, 0)))
        self.assertIsNone(kitty.cell_size(self.pty(24, 80, 800, 0)))


if __name__ == "__main__":
    unittest.main()
