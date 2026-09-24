"""The viewer pane: selection state, frame planning, and the terminal loop."""

from . import safety


class Selection:
    """Which history entry the viewer shows, held by content hash."""

    def __init__(self):
        self.entries = []
        self.selected = None

    def update(self, entries):
        self.entries = list(entries)
        if self.entries:
            self.selected = self.entries[-1].sha256

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
        return f"{self.index() + 1}/{len(self.entries)} {safety.display_text(entry.name)} {width}x{height}"
