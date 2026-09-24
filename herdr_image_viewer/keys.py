"""Conversation keys: which history an image belongs to."""

import hashlib

MAX_ID_LENGTH = 256


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


def directory_name(key):
    """A fixed-length name safe to use as a path component for any key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
