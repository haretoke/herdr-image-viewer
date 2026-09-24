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

    def test_a_wide_pane_places_the_thumbnail_grid_in_columns_on_the_right(self):
        # 120x20 cells = 1200x400 px: wider than tall. Two columns of 10x3 cells
        # plus a gap take 21 columns; the column before them separates the image.
        result = layout.compute(cols=120, rows=20, cell_w=CELL_W, cell_h=CELL_H, count=12)

        self.assertEqual(result.grid.orientation, "right")
        self.assertEqual((result.grid.columns, result.grid.rows), (2, 6))
        self.assertEqual(result.main, Rect(col=0, row=1, cols=98, rows=19))
        self.assertEqual(result.grid.cells[0], Rect(col=99, row=1, cols=10, rows=3))
        self.assertEqual(result.grid.cells[1], Rect(col=99, row=4, cols=10, rows=3))
        self.assertEqual(result.grid.cells[6], Rect(col=110, row=1, cols=10, rows=3))

    def test_a_pane_below_the_minimum_size_shows_no_thumbnails(self):
        cases = {
            "grid holds fewer than 3 below": (20, 12),
            "grid holds fewer than 3 on the right": (40, 6),
            "main image would get fewer than 4 rows": (22, 11),  # tall: 11 - title - separator - 6
            "main image would get fewer than 16 columns": (36, 10),  # wide: 36 - 21 - separator
        }
        for reason, (cols, rows) in cases.items():
            with self.subTest(reason):
                result = layout.compute(cols=cols, rows=rows, cell_w=CELL_W, cell_h=CELL_H, count=12)

                self.assertIsNone(result.grid)
                self.assertEqual(result.main, Rect(col=0, row=1, cols=cols, rows=rows - 1))

    def test_an_empty_history_lays_out_only_the_placeholder_area(self):
        result = layout.compute(cols=60, rows=40, cell_w=CELL_W, cell_h=CELL_H, count=0)

        self.assertIsNone(result.grid)
        self.assertEqual(result.main, Rect(col=0, row=1, cols=60, rows=39))


if __name__ == "__main__":
    unittest.main()
