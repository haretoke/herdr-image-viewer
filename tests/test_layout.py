import unittest

from herdr_image_viewer import layout
from herdr_image_viewer.layout import Rect

# 10x20 px cells make thumbnails 10x3 cells (about 96x60 px) with a 1-column gap.
CELL_W, CELL_H = 10, 20


class LayoutTest(unittest.TestCase):
    def test_a_tall_pane_places_the_thumbnail_grid_below_the_main_image(self):
        # 60x40 cells = 600x800 px: taller than wide.
        result = layout.compute(cols=60, rows=40, cell_w=CELL_W, cell_h=CELL_H, count=12)

        self.assertEqual(result.grid.orientation, "below")
        self.assertEqual((result.grid.columns, result.grid.rows), (5, 2))
        self.assertEqual(result.main, Rect(col=0, row=1, cols=60, rows=32))
        self.assertEqual(result.grid.cells[0], Rect(col=0, row=34, cols=10, rows=3))
        self.assertEqual(result.grid.cells[1], Rect(col=11, row=34, cols=10, rows=3))
        self.assertEqual(result.grid.cells[5], Rect(col=0, row=37, cols=10, rows=3))


if __name__ == "__main__":
    unittest.main()
