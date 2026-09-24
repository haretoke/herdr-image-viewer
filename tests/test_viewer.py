import unittest

from herdr_image_viewer import composite, limits
from herdr_image_viewer.herdr_api import HerdrError
from herdr_image_viewer.store import Entry
from herdr_image_viewer.viewer import KeyParser, Pane, Renderer, Selection, Viewer


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


class KeyParserTest(unittest.TestCase):
    def test_arrow_key_escape_sequences_split_across_reads_are_parsed(self):
        parser = KeyParser()

        self.assertEqual(parser.feed(b"\x1b"), [])
        self.assertEqual(parser.feed(b"["), [])
        self.assertEqual(parser.feed(b"D"), ["left"])
        self.assertEqual(parser.feed(b"hj\x1b[C\x1bOAlkq"), ["left", "down", "right", "up", "right", "up", "quit"])
        self.assertEqual(parser.feed(b"\x1b[Zx"), [])  # unknown sequences and keys are ignored


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
        self.lost_reasons = []
        self.failing = False
        self.attempts = 0
        self.main_error = None
        self.thumb_error = None
        self.thumb_attempts = 0
        self.thumbs_dropped = 0

    def lost(self):
        return self.lost_reasons.pop(0) if self.lost_reasons else None

    def send_main(self, data, width, height, placement):
        self.attempts += 1
        if self.failing:
            raise HerdrError("Herdr refused the graphics stream: layer limit")
        if self.main_error is not None:
            raise self.main_error
        self.main_frames.append((data, placement))

    def send_thumbs(self, data, width, height, placement):
        self.thumb_attempts += 1
        if self.thumb_error is not None:
            raise self.thumb_error
        self.thumb_frames.append(placement)

    def drop_thumbs(self):
        self.thumbs_dropped += 1

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

    def test_the_thumbnail_cache_stays_within_its_limit_dropping_the_least_recently_used(self):
        # Each 200x100 image becomes a 96x48 thumbnail: normal + dimmed RGB = 27,648 bytes.
        renderer = Renderer(self.display, self.images, thumb_cache_bytes=2 * 27_648)

        renderer.draw(self.selection, PANE)

        self.assertLessEqual(renderer.thumb_cache_size, 2 * 27_648)
        self.assertEqual([key[0] for key in renderer.thumb_cache], [entry(2).sha256, entry(3).sha256])
        self.selection.move("left", renderer.grid)
        renderer.draw(self.selection, PANE)  # the page is redrawn; 1.png had been dropped
        self.assertEqual([name for name, _, _ in self.images.thumb_conversions].count("1.png"), 2)
        self.assertEqual(limits.THUMB_CACHE_BYTES, 32 * 1024 * 1024)


class ViewerTest(unittest.TestCase):
    def setUp(self):
        self.images = FakeImages()
        self.display = FakeDisplay()
        self.entries = [entry(number) for number in range(1, 13)]
        self.pane = PANE
        self.now = 0.0
        self.viewer = Viewer(Renderer(self.display, self.images), lambda: self.entries,
                             lambda: self.pane, clock=lambda: self.now)
        self.viewer.step()

    def shown(self):
        return [data.split()[1].decode() for data, _ in self.display.main_frames]

    def test_repeated_keys_coalesce_into_one_redraw_of_the_last_selection(self):
        self.viewer.on_input(b"hhh")
        self.viewer.step()

        self.assertEqual(self.shown(), ["12.png", "9.png"])

    def test_the_viewer_picks_up_a_newly_published_image_without_input(self):
        self.entries = self.entries + [entry(13)]

        self.viewer.step()

        self.assertEqual(self.shown(), ["12.png", "13.png"])

    def test_a_move_blocked_at_an_end_sends_nothing(self):
        self.viewer.on_input(b"l")  # already on the newest image
        self.viewer.step()

        self.assertEqual(self.shown(), ["12.png"])
        self.assertEqual(len(self.display.thumb_frames), 1)

    def test_sigwinch_bursts_are_debounced_and_identical_frames_are_not_resent(self):
        for at, cols in ((0.0, 58), (0.1, 56), (0.2, 50)):
            self.now = at
            self.pane = Pane(cols=cols, rows=40, cell_w=10, cell_h=20)
            self.viewer.on_resize()
        self.now = 0.45
        self.viewer.step()
        self.assertEqual(len(self.display.main_frames), 1)  # still settling

        self.now = 0.5
        self.viewer.step()
        self.now = 1.2  # the late re-fit finds nothing changed
        self.viewer.step()

        self.assertEqual(len(self.display.main_frames), 2)
        self.assertEqual(len(self.display.thumb_frames), 2)
        # The 200x100 px image (20x5 cells) is centered in the final 50 columns.
        self.assertEqual(self.display.main_frames[-1][1]["viewport_col"], 15)

    def test_a_viewer_started_with_a_stale_pty_size_refits_after_the_next_sigwinch(self):
        # Opened in a hidden tab: the pty still has the whole tab's nominal size.
        self.pane = Pane(cols=140, rows=40, cell_w=10, cell_h=20)
        viewer = Viewer(Renderer(self.display, self.images), lambda: self.entries,
                        lambda: self.pane, clock=lambda: self.now)
        viewer.step()
        stale = self.display.main_frames[-1][1]

        self.pane = Pane(cols=60, rows=40, cell_w=10, cell_h=20)  # the tab is shown
        viewer.on_resize()
        self.now += 0.3
        viewer.step()

        self.assertNotEqual(self.display.main_frames[-1][1], stale)
        self.assertEqual(self.display.main_frames[-1][1]["viewport_col"], (60 - 20) // 2)

    def test_a_lost_stream_is_restored_without_input_with_backoff_and_gives_up(self):
        self.display.lost_reasons.append("Herdr closed the connection")
        self.now = 10.0
        self.viewer.step()  # noticed; the first retry waits a second
        self.assertEqual(len(self.display.main_frames), 1)
        self.now = 11.0
        self.viewer.step()
        self.assertEqual(len(self.display.main_frames), 2)  # restored without any input

        self.display.failing = True
        self.display.lost_reasons.append("Herdr closed the connection")
        self.now = 20.0
        self.viewer.step()
        attempts_at = []
        for second in range(21, 200):
            before = self.display.attempts
            self.now = float(second)
            self.viewer.step()
            if self.display.attempts > before:
                attempts_at.append(second)

        self.assertEqual(attempts_at, [21, 23, 27, 35, 51, 81])  # 1, 2, 4, 8, 16, 30 s apart
        self.assertIn("cannot show images", self.display.titles[-1])
        self.assertIn("layer limit", self.display.titles[-1])

    def test_a_resource_error_drops_the_thumbnails_first_then_reports_the_main_image(self):
        limit = HerdrError("pane graphics layer limit reached", code="layer_limit")
        self.display.thumb_error = limit
        attempts = self.display.thumb_attempts
        self.viewer.on_input(b"h")
        self.viewer.step()  # the thumbnail stream is refused: thumbnails are dropped
        self.viewer.on_input(b"h")
        self.viewer.step()

        self.assertEqual(self.display.thumb_attempts, attempts + 1)
        self.assertEqual(self.display.thumbs_dropped, 1)
        self.assertEqual(self.shown(), ["12.png", "11.png", "10.png"])
        self.assertTrue(self.display.titles[-1].endswith("[no thumbnails]"))

        self.display.main_error = limit
        self.viewer.on_input(b"h")
        self.viewer.step()
        self.assertEqual(self.display.titles[-1], "image unavailable: pane graphics layer limit reached")


if __name__ == "__main__":
    unittest.main()
