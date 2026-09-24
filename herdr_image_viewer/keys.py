"""Conversation keys: which history an image belongs to."""

import hashlib

MAX_ID_LENGTH = 256
MAX_KEY_LENGTH = 1024
NAMESPACES = ("claude", "codex", "pane")


def conversation_key(payload, socket_path, caller_pane):
    session_id = payload.get("session_id")
    if usable_id(session_id):
        return "claude:" + session_id
    # Documented limitation: without a conversation id every conversation in
    # the same pane shares one history.
    return f"pane:{socket_path}#{caller_pane}"


def usable_id(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LENGTH
        and value.isprintable()
    )


def valid_key(key):
    """A key conversation_key could have produced (pane keys carry a socket path)."""
    namespace, separator, rest = key.partition(":")
    return (
        separator == ":"
        and namespace in NAMESPACES
        and len(rest) > 0
        and len(key) <= MAX_KEY_LENGTH
        and key.isprintable()
    )


def directory_name(key):
    """A fixed-length name safe to use as a path component for any key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
