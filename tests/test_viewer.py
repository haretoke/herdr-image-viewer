import unittest

from herdr_image_viewer.store import Entry
from herdr_image_viewer.viewer import Selection


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


if __name__ == "__main__":
    unittest.main()
