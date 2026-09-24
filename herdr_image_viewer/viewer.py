"""The viewer pane: selection state, frame planning, and the terminal loop."""

from . import layout, safety


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
        self.entries = list(entries)
        if not self.entries:
            return
        if self.follow_latest:
            self.selected = self.entries[-1].sha256
        elif any(entry.sha256 not in previous for entry in self.entries):
            self.has_new = True

    def move(self, direction, grid):
        position = self.index()
        if position is None:
            return
        target = layout.move(grid, len(self.entries), position, direction)
        self.selected = self.entries[target].sha256
        self.follow_latest = target == len(self.entries) - 1

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
