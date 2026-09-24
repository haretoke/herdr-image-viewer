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

    def test_unknown_or_zero_sizes_produce_no_layout(self):
        for cols, rows, cell_w, cell_h in (
            (0, 40, CELL_W, CELL_H),
            (60, 0, CELL_W, CELL_H),
            (60, 40, 0, CELL_H),
            (60, 40, CELL_W, None),
            (None, 40, CELL_W, CELL_H),
            (60, -1, CELL_W, CELL_H),
        ):
            with self.subTest(size=(cols, rows, cell_w, cell_h)):
                self.assertIsNone(layout.compute(cols=cols, rows=rows, cell_w=cell_w, cell_h=cell_h, count=3))


BELOW = layout.compute(cols=60, rows=40, cell_w=CELL_W, cell_h=CELL_H, count=12).grid  # 5 x 2
RIGHT = layout.compute(cols=120, rows=20, cell_w=CELL_W, cell_h=CELL_H, count=12).grid  # 2 x 6


class MoveTest(unittest.TestCase):
    def assertMoves(self, grid, count, cases):
        for (start, direction), expected in cases.items():
            with self.subTest(start=start, direction=direction):
                self.assertEqual(layout.move(grid, count, start, direction), expected)

    def test_moves_go_left_down_up_right_and_stop_only_at_the_ends_of_the_history(self):
        # Below: row-major, five per row; left/right continue across rows.
        self.assertMoves(BELOW, 12, {
            (0, "right"): 1, (11, "right"): 11, (0, "left"): 0, (5, "left"): 4,
            (1, "down"): 6, (5, "down"): 10, (6, "up"): 1, (2, "up"): 2,
        })
        # Right: column-major, six per column; up/down continue across columns.
        self.assertMoves(RIGHT, 12, {
            (0, "down"): 1, (11, "down"): 11, (6, "up"): 5, (0, "up"): 0,
            (1, "right"): 7, (7, "left"): 1, (3, "left"): 3,
        })

    def test_moving_past_the_visible_page_shows_the_next_or_previous_page(self):
        # Below holds 10 per page, Right 12; the page is the window around the selection.
        self.assertEqual(layout.page(BELOW, 23, 9), range(0, 10))
        after = layout.move(BELOW, 23, 9, "right")
        self.assertEqual(layout.page(BELOW, 23, after), range(10, 20))
        self.assertEqual(layout.page(BELOW, 23, layout.move(BELOW, 23, after, "left")), range(0, 10))
        self.assertEqual(layout.page(BELOW, 23, layout.move(BELOW, 23, 7, "down")), range(10, 20))
        self.assertEqual(layout.page(BELOW, 23, 22), range(20, 23))
        self.assertEqual(layout.page(RIGHT, 30, layout.move(RIGHT, 30, 11, "down")), range(12, 24))


if __name__ == "__main__":
    unittest.main()
