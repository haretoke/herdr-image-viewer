import unittest

from herdr_image_viewer import composite
from herdr_image_viewer.store import Entry
from herdr_image_viewer.viewer import Pane, Renderer, Selection


def entry(number, name=None):
    return Entry(sha256=f"{number:064x}", name=name or f"{number}.png", source=f"/tmp/{number}.png",
                 format="png", published_at=float(number))


class SelectionTest(unittest.TestCase):
    def test_on_start_the_newest_entry_is_selected_and_titled(self):
        selection = Selection()

        selection.update([entry(1), entry(2), entry(3, "shot.png")])

        self.assertEqual(selection.current().name, "shot.png")
        self.assertEqual(selection.title(size=(1050, 966)), "3/3 shot.png 1050x966")

    def test_while_following_the_latest_a_new_image_becomes_the_selection(self):
        selection = Selection()
        selection.update([entry(1), entry(2)])

        selection.update([entry(1), entry(2), entry(3)])

        self.assertEqual(selection.current(), entry(3))

    def test_after_moving_to_an_older_image_a_new_image_keeps_the_selection_and_marks_new(self):
        selection = Selection()
        selection.update([entry(1), entry(2), entry(3)])
        selection.move("left", grid=None)

        selection.update([entry(1), entry(2), entry(3), entry(4)])

        self.assertEqual(selection.current(), entry(2))
        self.assertTrue(selection.has_new)
        self.assertEqual(selection.title(size=(10, 10)), "2/4 2.png 10x10 [new]")

    def test_moving_back_to_the_newest_image_follows_again_and_clears_new(self):
        selection = Selection()
        selection.update([entry(1), entry(2), entry(3)])
        selection.move("left", grid=None)
        selection.update([entry(1), entry(2), entry(3), entry(4)])

        selection.move("right", grid=None)
        selection.move("right", grid=None)

        self.assertEqual(selection.current(), entry(4))
        self.assertTrue(selection.follow_latest)
        self.assertFalse(selection.has_new)
        selection.update([entry(1), entry(2), entry(3), entry(4), entry(5)])
        self.assertEqual(selection.current(), entry(5))

    def test_the_selection_follows_its_content_when_entries_move_or_it_is_dropped(self):
        selection = Selection()
        selection.update([entry(1), entry(2), entry(3)])
        selection.move("left", grid=None)

        selection.update([entry(1), entry(3), entry(2)])  # 2 was republished
        self.assertEqual(selection.current(), entry(2))

        selection.move("left", grid=None)
        selection.move("left", grid=None)
        self.assertEqual(selection.current(), entry(1))
        selection.update([entry(3), entry(2), entry(4)])  # 1 fell out of the history
        self.assertEqual(selection.current(), entry(3))


class FakeImages:
    """Stands in for the converters: counts conversions, returns tiny data."""

    def __init__(self):
        self.main_conversions = []
        self.thumb_conversions = []

    def size(self, entry):
        return 200, 100

    def main_png(self, entry, width, height):
        self.main_conversions.append((entry.name, width, height))
        return f"main {entry.name} {width}x{height}".encode()

    def thumb(self, entry, width, height):
        self.thumb_conversions.append((entry.name, width, height))
        return composite.prepare(width, height, bytes([90, 90, 90, 255]) * (width * height))


class FakeDisplay:
    def __init__(self):
        self.main_frames = []
        self.thumb_frames = []
        self.titles = []

    def send_main(self, data, width, height, placement):
        self.main_frames.append((data, placement))

    def send_thumbs(self, data, width, height, placement):
        self.thumb_frames.append(placement)

    def show_title(self, text):
        self.titles.append(text)


PANE = Pane(cols=60, rows=40, cell_w=10, cell_h=20)  # tall: two rows of five thumbnails below


class RendererTest(unittest.TestCase):
    def setUp(self):
        self.images = FakeImages()
        self.display = FakeDisplay()
        self.renderer = Renderer(self.display, self.images)
        self.selection = Selection()
        self.selection.update([entry(1), entry(2), entry(3)])

    def test_moving_the_selection_converts_only_a_newly_selected_image(self):
        self.renderer.draw(self.selection, PANE)
        self.assertEqual([name for name, _, _ in self.images.main_conversions], ["3.png"])
        self.assertEqual(len(self.images.thumb_conversions), 3)

        self.selection.move("left", self.renderer.grid)
        self.renderer.draw(self.selection, PANE)
        self.selection.move("right", self.renderer.grid)
        self.renderer.draw(self.selection, PANE)

        self.assertEqual([name for name, _, _ in self.images.main_conversions], ["3.png", "2.png"])
        self.assertEqual(len(self.images.thumb_conversions), 3)
        self.assertEqual([data.split()[1] for data, _ in self.display.main_frames], [b"3.png", b"2.png", b"3.png"])
        self.assertEqual(len(self.display.thumb_frames), 3)


if __name__ == "__main__":
    unittest.main()
