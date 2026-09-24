"""Herdr socket API: one-shot requests and pane graphics streams.

Ported from devcon-herdr's herdr-image-preview (socket_request, GraphicsStream).
"""

import json
import select
import socket
import uuid

MAX_LINE_BYTES = 1024 * 1024
REJECTION_WAIT_SECONDS = 0.2


class HerdrError(Exception):
    pass


def error_text(details):
    if isinstance(details, dict):
        return details.get("message") or details.get("code") or str(details)
    return str(details)


def rejection(line):
    """The error message in a reply line, or None if it is not an error."""
    if not line:
        return "Herdr closed the connection"
    try:
        response = json.loads(line)
    except (TypeError, ValueError):
        return "Herdr returned invalid JSON"
    if "error" not in response:
        return None
    return error_text(response.get("error"))


class GraphicsStream:
    """A pane.graphics.stream connection owning one layer of a pane.

    Herdr replies "ok" to the open; afterwards it replies to a frame only to
    reject it and then closes the stream. Closing the stream removes the layer.
    """

    def __init__(self, socket_path, pane_id, layer_id, z_index):
        self.socket_path = socket_path
        self.params = {"pane_id": pane_id, "layer_id": layer_id, "z_index": z_index}
        self.client = None
        self.reader = None

    def open(self):
        request = {"id": f"herdr-image-viewer:{uuid.uuid4().hex}", "method": "pane.graphics.stream",
                   "params": self.params}
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10)
        try:
            client.connect(self.socket_path)
            client.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            reader = client.makefile("rb")
            line = reader.readline(MAX_LINE_BYTES + 1)
        except (OSError, TimeoutError) as error:
            client.close()
            raise HerdrError(f"could not open a graphics stream: {error}") from error
        refused = rejection(line)
        if refused:
            reader.close()
            client.close()
            raise HerdrError(f"Herdr refused the graphics stream: {refused}")
        self.client, self.reader = client, reader

    def is_open(self):
        return self.client is not None

    def lost(self):
        """Without blocking: why an open stream ended (a late rejection or EOF),
        closing it, or None while it is healthy or was never opened."""
        if self.client is None:
            return None
        try:
            if not select.select([self.client], [], [], 0)[0]:
                return None
            line = self.reader.readline(MAX_LINE_BYTES + 1)
        except (OSError, ValueError) as error:
            reason = f"the graphics stream failed: {error}"
        else:
            reason = rejection(line) or "unexpected reply"
        self.close()
        return reason

    def send(self, data, width, height, placement, image_format="png"):
        """Send one frame; a rejection closes the stream and raises HerdrError."""
        if self.client is None:
            self.open()
        header = {
            "format": image_format,
            "image_width": width,
            "image_height": height,
            "data_length": len(data),
            "placement": placement,
        }
        try:
            self.client.sendall(json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n" + data)
            # Success has no reply; a rejection arrives as an error line, then EOF.
            if not select.select([self.client], [], [], REJECTION_WAIT_SECONDS)[0]:
                return
            line = self.reader.readline(MAX_LINE_BYTES + 1)
        except (OSError, TimeoutError) as error:
            self.close()
            raise HerdrError(f"the graphics stream failed: {error}") from error
        self.close()
        raise HerdrError(f"Herdr rejected the frame: {rejection(line) or 'unexpected reply'}")

    def close(self):
        # Close the makefile() reader too: while it is open the connection, and
        # the layer, stay (spike 0-3).
        for handle in (self.reader, self.client):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        self.client = self.reader = None
