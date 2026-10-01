"""Herdr socket API: one-shot requests.

Ported from devcon-herdr's herdr-image-preview (socket_request). The pane
graphics stream went away with Herdr 0.9.2; images are Kitty graphics now.
"""

import json
import socket
import uuid

MAX_LINE_BYTES = 1024 * 1024


RESOURCE_CODES = {"layer_limit", "graphics_budget_exceeded"}


class HerdrError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code

    @property
    def resource(self):
        """Herdr ran out of graphics layers or memory (not a fault of this frame)."""
        return self.code in RESOURCE_CODES


def error_text(details):
    if isinstance(details, dict):
        return details.get("message") or details.get("code") or str(details)
    return str(details)


def reply_error(line):
    """(message, code) of an error reply line, or None if it is not an error."""
    if not line:
        return "Herdr closed the connection", None
    try:
        response = json.loads(line)
    except (TypeError, ValueError):
        return "Herdr returned invalid JSON", None
    if "error" not in response:
        return None
    details = response.get("error")
    code = details.get("code") if isinstance(details, dict) else None
    return error_text(details), code


def request(socket_path, method, params, timeout=10):
    """One request on its own connection; returns the result or raises HerdrError."""
    request_id = f"herdr-image-viewer:{uuid.uuid4().hex}"
    encoded = json.dumps({"id": request_id, "method": method, "params": params},
                         separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(socket_path)
            client.sendall(encoded)
            with client.makefile("rb") as reader:
                line = reader.readline(MAX_LINE_BYTES + 1)
    except (OSError, TimeoutError) as error:
        raise HerdrError(f"could not reach Herdr: {error}") from error
    error = reply_error(line)
    if error is not None:
        raise HerdrError(f"Herdr rejected {method}: {error[0]}", error[1])
    return json.loads(line).get("result", {})
