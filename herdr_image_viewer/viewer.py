"""The viewer pane: selection state, frame planning, and the terminal loop."""

import time
from collections import OrderedDict
from dataclasses import dataclass

from . import composite, herdr_api, layout, limits, png, safety

MAIN_CACHE_ENTRIES = 8
REFIT_DELAYS = (0.3, 1.0)
RETRY_DELAYS = (1, 2, 4, 8, 16, 30)  # after a lost stream; then give up


@dataclass(frozen=True)
class Pane:
    """The viewer's own pane: its pty size and the client's cell size in pixels."""

    cols: int
    rows: int
    cell_w: int
    cell_h: int


class Selection:
    """Which history entry the viewer shows, held by content hash.

    While follow_latest is on, each new image becomes the selection. Moving to
    an older image turns it off; images arriving then set has_new instead.
    """

    def __init__(self):
        self.entries = []
        self.selected = None
        self.follow_latest = True
        self.has_new = False

    def update(self, entries):
        previous = {entry.sha256 for entry in self.entries}
        previous_index = self.index()
        self.entries = list(entries)
        if not self.entries:
            return
        if self.follow_latest:
            self.selected = self.entries[-1].sha256
            return
        if any(entry.sha256 not in previous for entry in self.entries):
            self.has_new = True
        if self.index() is None:  # the selected entry fell out: take its nearest neighbor
            nearest = min(previous_index or 0, len(self.entries) - 1)
            self.selected = self.entries[nearest].sha256

    def move(self, direction, grid):
        position = self.index()
        if position is None:
            return
        target = layout.move(grid, len(self.entries), position, direction)
        self.selected = self.entries[target].sha256
        self.follow_latest = target == len(self.entries) - 1
        if self.follow_latest:
            self.has_new = False

    def index(self):
        for position, entry in enumerate(self.entries):
            if entry.sha256 == self.selected:
                return position
        return None

    def current(self):
        position = self.index()
        return None if position is None else self.entries[position]

    def title(self, size):
        entry = self.current()
        width, height = size
        marker = " [new]" if self.has_new else ""
        return (
            f"{self.index() + 1}/{len(self.entries)} {safety.display_text(entry.name)} "
            f"{width}x{height}{marker}"
        )


KEYS = {b"h": "left", b"j": "down", b"k": "up", b"l": "right", b"q": "quit", b"Q": "quit"}
ARROWS = {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}


class KeyParser:
    """Keys from terminal input; escape sequences may arrive split across reads."""

    def __init__(self):
        self.pending = b""

    def feed(self, data):
        keys = []
        buffer = self.pending + data
        self.pending = b""
        position = 0
        while position < len(buffer):
            byte = buffer[position:position + 1]
            if byte != b"\x1b":
                if byte in KEYS:
                    keys.append(KEYS[byte])
                position += 1
                continue
            sequence = buffer[position:position + 3]
            if len(sequence) < 3:  # ESC [ X or ESC O X not complete yet
                self.pending = sequence
                break
            if sequence[1:2] in (b"[", b"O") and sequence[2:3] in ARROWS:
                keys.append(ARROWS[sequence[2:3]])
            position += 3
        return keys


class Viewer:
    """The pane loop's decisions, free of terminal and socket I/O.

    Input changes the selection right away; drawing happens once per step, so
    a burst of keys redraws only the last selection.
    """

    def __init__(self, renderer, read_history, read_pane, clock=time.monotonic):
        self.renderer = renderer
        self.read_history = read_history
        self.read_pane = read_pane
        self.clock = clock
        self.selection = Selection()
        self.selection.update(read_history())
        self.dirty = True
        self.refits = []
        self.retry_at = None
        self.failures = 0
        self.gave_up = False
        self.keys = KeyParser()
        self.quit = False

    def on_resize(self):
        """Re-fit once the layout settles and once more late (as the old
        follower did); identical frames are not re-sent."""
        now = self.clock()
        self.refits = [now + delay for delay in REFIT_DELAYS]

    def on_input(self, data):
        for key in self.keys.feed(data):
            if key == "quit":
                self.quit = True
                continue
            before = self.selection.selected
            self.selection.move(key, self.renderer.grid)
            self.dirty = self.dirty or self.selection.selected != before

    def step(self):
        now = self.clock()
        entries = self.read_history()
        if entries != self.selection.entries:  # a publish (or GC) changed the history
            self.selection.update(entries)
            self.dirty = True
        reason = self.renderer.display.lost()
        if reason is not None:
            self.retry_later(reason)
        if self.gave_up:
            return
        if self.retry_at is not None:
            if now < self.retry_at:
                return
            self.retry_at = None
            self.renderer.invalidate()  # everything must be sent again
            self.dirty = True
        if self.refits and now >= self.refits[0]:
            self.refits = [deadline for deadline in self.refits if deadline > now]
            self.dirty = True
        if self.dirty:
            try:
                self.renderer.draw(self.selection, self.read_pane())
            except herdr_api.HerdrError as error:
                if error.resource:
                    self.renderer.display.show_title(f"image unavailable: {safety.display_text(str(error))}")
                self.retry_later(str(error))
                return
            self.dirty = False
            self.failures = 0

    def retry_later(self, reason):
        """Redraw after the next backoff delay, or give up with a message."""
        if self.failures >= len(RETRY_DELAYS):
            self.gave_up = True
            self.renderer.display.show_title(f"cannot show images: {safety.display_text(reason)}")
            return
        self.retry_at = self.clock() + RETRY_DELAYS[self.failures]
        self.failures += 1


def herdr_placement(col, row, cols, rows):
    return {"viewport_col": col, "viewport_row": row, "grid_cols": cols, "grid_rows": rows}


def fit_pixels(width, height, box_width, box_height):
    """An image's size fitted inside a pixel box, never upscaled."""
    scale = min(box_width / width, box_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


class Renderer:
    """Turns the selection into frames for the display.

    Each image is converted once per size: main images are kept for the last
    few selections, thumbnails for as long as they are shown.
    """

    def __init__(self, display, images, thumb_cache_bytes=limits.THUMB_CACHE_BYTES):
        self.display = display
        self.images = images
        self.grid = None
        self.main_cache = OrderedDict()
        self.thumb_cache = OrderedDict()
        self.thumb_cache_size = 0
        self.thumb_cache_bytes = thumb_cache_bytes
        self.sent = {}  # what each frame last showed, to skip identical re-sends
        self.thumbs_enabled = True

    def invalidate(self):
        """Forget what was sent, so the next draw sends every frame again."""
        self.sent.clear()

    def draw(self, selection, pane):
        result = layout.compute(pane.cols, pane.rows, pane.cell_w, pane.cell_h, len(selection.entries))
        if result is None:
            return
        if not self.thumbs_enabled:
            result = layout.Layout(main=layout.Rect(col=0, row=1, cols=pane.cols, rows=pane.rows - 1), grid=None)
        self.grid = result.grid
        current = selection.current()
        if current is None:
            return
        size = self.images.size(current)
        placement = layout.fit(*size, result.main, pane.cell_w, pane.cell_h)
        main_key = (current.sha256, placement)
        if main_key != self.sent.get("main"):
            self.display.send_main(
                self.main_png(current, placement.width, placement.height),
                placement.width,
                placement.height,
                herdr_placement(placement.col, placement.row, placement.cols, placement.rows),
            )
            self.sent["main"] = main_key
        if result.grid is not None:
            try:
                self.draw_thumbs(selection, result.grid, pane)
            except herdr_api.HerdrError as error:
                if not error.resource:
                    raise
                # Out of layers or graphics memory: keep the main image, drop thumbnails.
                self.thumbs_enabled = False
                self.display.drop_thumbs()
        title = selection.title(size) + ("" if self.thumbs_enabled else " [no thumbnails]")
        if title != self.sent.get("title"):
            self.display.show_title(title)
            self.sent["title"] = title

    def draw_thumbs(self, selection, grid, pane):
        index = selection.index()
        visible = layout.page(grid, len(selection.entries), index)
        left = min(cell.col for cell in grid.cells)
        top = min(cell.row for cell in grid.cells)
        cols = max(cell.col + cell.cols for cell in grid.cells) - left
        rows = max(cell.row + cell.rows for cell in grid.cells) - top
        width, height = cols * pane.cell_w, rows * pane.cell_h
        thumbs_key = (tuple(selection.entries[p].sha256 for p in visible), index, grid, pane)
        if thumbs_key == self.sent.get("thumbs"):
            return
        self.sent["thumbs"] = thumbs_key
        slots, thumbs = [], []
        for cell, position in zip(grid.cells, visible):
            slot = composite.Slot(
                x=(cell.col - left) * pane.cell_w,
                y=(cell.row - top) * pane.cell_h,
                width=cell.cols * pane.cell_w,
                height=cell.rows * pane.cell_h,
            )
            entry = selection.entries[position]
            inner = 2 * composite.BORDER_PX
            thumb_size = fit_pixels(*self.images.size(entry), slot.width - inner, slot.height - inner)
            slots.append(slot)
            thumbs.append(self.thumb(entry, *thumb_size))
        canvas = composite.render(width, height, slots, thumbs, selected=index - visible.start)
        self.display.send_thumbs(png.encode_rgb(width, height, canvas), width, height,
                                 herdr_placement(left, top, cols, rows))

    def main_png(self, entry, width, height):
        key = (entry.sha256, width, height)
        if key in self.main_cache:
            self.main_cache.move_to_end(key)
        else:
            self.main_cache[key] = self.images.main_png(entry, width, height)
            while len(self.main_cache) > MAIN_CACHE_ENTRIES:
                self.main_cache.popitem(last=False)
        return self.main_cache[key]

    def thumb(self, entry, width, height):
        key = (entry.sha256, width, height)
        if key in self.thumb_cache:
            self.thumb_cache.move_to_end(key)
            return self.thumb_cache[key]
        thumb = self.images.thumb(entry, width, height)
        self.thumb_cache[key] = thumb
        self.thumb_cache_size += len(thumb.normal) + len(thumb.dimmed)
        while self.thumb_cache_size > self.thumb_cache_bytes:
            _, dropped = self.thumb_cache.popitem(last=False)
            self.thumb_cache_size -= len(dropped.normal) + len(dropped.dimmed)
        return thumb
