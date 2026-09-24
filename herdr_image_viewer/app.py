"""The viewer process: terminal, Herdr display, store-backed images, and loop."""

import os
import select
import signal
import sys
import termios
import time
import traceback
import tty
from pathlib import Path

from . import composite, herdr_api, imaging, safety
from .store import Store
from .viewer import Pane, Renderer, Viewer

STEP_SECONDS = 0.25
CONVERTED_CACHE_ENTRIES = 4


class Terminal:
    """The viewer's pane: alternate screen, hidden cursor, cbreak keys, a title line."""

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.saved = None

    def __enter__(self):
        self.saved = termios.tcgetattr(self.stdin.fileno())
        tty.setcbreak(self.stdin.fileno())
        self.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        return self

    def __exit__(self, *_):
        self.write("\x1b[?25h\x1b[?1049l")
        try:
            termios.tcsetattr(self.stdin.fileno(), termios.TCSADRAIN, self.saved)
        except (OSError, termios.error):
            pass  # the pane is already gone

    def write(self, text):
        try:
            self.stdout.write(text)
            self.stdout.flush()
        except OSError:
            pass

    def size(self):
        try:
            size = os.get_terminal_size(self.stdout.fileno())
        except OSError:
            return 0, 0
        return size.columns, size.lines

    def show_title(self, text):
        columns = max(1, self.size()[0])
        self.write("\x1b[H\x1b[2K" + safety.display_text(text)[: columns - 1])

    def read(self, timeout):
        """Input bytes, b"" when there is none, or None once the pane is gone."""
        try:
            if not select.select([self.stdin], [], [], timeout)[0]:
                return b""
            data = os.read(self.stdin.fileno(), 1024)
        except OSError:
            return None
        return data or None


class HerdrDisplay:
    """Two stream layers over the viewer's pane: the main image and the thumbnails."""

    def __init__(self, socket_path, pane_id, terminal):
        self.socket_path = socket_path
        self.pane_id = pane_id
        self.terminal = terminal
        self.main = herdr_api.GraphicsStream(socket_path, pane_id, "main", 10)
        self.thumbs = herdr_api.GraphicsStream(socket_path, pane_id, "thumbs", 20)

    def cell_size(self):
        info = herdr_api.request(self.socket_path, "pane.graphics.info", {"pane_id": self.pane_id})
        return info.get("cell_width_px"), info.get("cell_height_px")

    def send_main(self, data, width, height, placement):
        self.main.send(data, width, height, placement)

    def send_thumbs(self, data, width, height, placement):
        self.thumbs.send(data, width, height, placement)

    def clear_main(self):
        self.main.close()  # closing a stream removes its layer

    def clear_thumbs(self):
        self.thumbs.close()

    def drop_thumbs(self):
        self.thumbs.close()

    def lost(self):
        return self.main.lost() or self.thumbs.lost()

    def show_title(self, text):
        self.terminal.show_title(text)

    def close(self):
        self.main.close()
        self.thumbs.close()


class StoreImages:
    """Images of one conversation, converted from the archive once per size."""

    def __init__(self, store, key):
        self.store = store
        self.key = key
        self.converted = {}

    def png(self, entry):
        if entry.sha256 not in self.converted:
            data = self.store.archive_path(self.key, entry).read_bytes()
            self.converted[entry.sha256] = imaging.to_png(data, entry.format)
            while len(self.converted) > CONVERTED_CACHE_ENTRIES:
                self.converted.pop(next(iter(self.converted)))
        return self.converted[entry.sha256]

    def size(self, entry):
        return imaging.png_size(self.png(entry))

    def main_png(self, entry, width, height):
        data = self.png(entry)
        if imaging.png_size(data) == (width, height):
            return data
        return imaging.resize(data, width, height)

    def thumb(self, entry, width, height):
        return composite.prepare(width, height, imaging.thumbnail_rgba(self.png(entry), width, height))


def run(viewer, terminal):
    """Step the viewer until q, a stop signal, or the pane going away."""
    stopping = []
    signal.signal(signal.SIGWINCH, lambda *_: viewer.on_resize())
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.append(True))
    while not stopping and not viewer.quit:
        viewer.step()
        data = terminal.read(STEP_SECONDS)
        if data is None:
            break
        viewer.on_input(data)


def run_from_environment(environ):
    """Run the viewer; an unexpected error is logged before exiting with 1,
    since a crashing pane closes and takes its output with it (spike 0-2)."""
    root = Path(environ["HERDR_IMAGE_VIEWER_STORE"])
    try:
        return run_viewer(environ, root)
    except Exception:
        log_error(root / "viewer.log", traceback.format_exc())
        return 1


def log_error(path, text):
    try:
        safety.private_directory(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} viewer error\n{text}\n")
    except OSError:
        pass  # nowhere left to report it


def run_viewer(environ, root):
    store = Store(root)
    key = environ["HERDR_IMAGE_VIEWER_CONVERSATION"]
    terminal = Terminal()
    display = HerdrDisplay(environ["HERDR_SOCKET_PATH"], environ["HERDR_PANE_ID"], terminal)

    def read_pane():
        cols, rows = terminal.size()
        cell_w, cell_h = display.cell_size()
        return Pane(cols=cols, rows=rows, cell_w=cell_w, cell_h=cell_h)

    viewer = Viewer(Renderer(display, StoreImages(store, key)), lambda: store.history(key), read_pane)
    try:
        with terminal:
            run(viewer, terminal)
    finally:
        display.close()
    return 0
