import unittest

from herdr_image_viewer import composite
from herdr_image_viewer.composite import Slot


def solid_rgba(width, height, rgba):
    return bytes(rgba) * (width * height)


def pixel(canvas, width, x, y):
    offset = (y * width + x) * 3
    return tuple(canvas[offset:offset + 3])


def dimmed(rgb):
    return tuple(composite.DIM_TABLE[value] for value in rgb)


class RenderTest(unittest.TestCase):
    # Three 20x10 px cell boxes one pixel apart on a 62x10 canvas.
    SLOTS = [Slot(x=0, y=0, width=20, height=10), Slot(x=21, y=0, width=20, height=10),
             Slot(x=42, y=0, width=20, height=10)]

    def thumbs(self):
        return [composite.prepare(12, 6, solid_rgba(12, 6, color))
                for color in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))]

    def test_thumbnails_land_in_their_cells_and_the_selection_is_highlighted(self):
        canvas = composite.render(62, 10, self.SLOTS, self.thumbs(), selected=1)

        self.assertEqual(pixel(canvas, 62, 10, 5), dimmed((255, 0, 0)))
        self.assertEqual(pixel(canvas, 62, 31, 5), (0, 255, 0))
        self.assertEqual(pixel(canvas, 62, 52, 5), dimmed((0, 0, 255)))
        self.assertEqual(pixel(canvas, 62, 21, 0), composite.HIGHLIGHT)   # selected box edge
        self.assertEqual(pixel(canvas, 62, 40, 9), composite.HIGHLIGHT)
        self.assertEqual(pixel(canvas, 62, 0, 0), composite.BACKGROUND)   # unselected box edge
        self.assertEqual(pixel(canvas, 62, 20, 5), composite.BACKGROUND)  # gap between boxes

    def test_unselected_thumbnails_are_dimmed_once_not_again_on_every_move(self):
        thumbs = self.thumbs()
        for selected in (0, 1, 2, 0, 2):
            canvas = composite.render(62, 10, self.SLOTS, thumbs, selected=selected)

        self.assertEqual(pixel(canvas, 62, 31, 5), dimmed((0, 255, 0)))
        self.assertEqual(pixel(canvas, 62, 10, 5), dimmed((255, 0, 0)))
        self.assertEqual(pixel(canvas, 62, 52, 5), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
