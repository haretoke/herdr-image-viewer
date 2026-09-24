"""Conversation keys: which history an image belongs to."""


def conversation_key(payload, socket_path, caller_pane):
    if "session_id" in payload:
        return "claude:" + payload["session_id"]
    # Documented limitation: without a conversation id every conversation in
    # the same pane shares one history.
    return f"pane:{socket_path}#{caller_pane}"
