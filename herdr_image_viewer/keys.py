"""Conversation keys: which history an image belongs to."""


def conversation_key(payload, socket_path, caller_pane):
    return "claude:" + payload["session_id"]
