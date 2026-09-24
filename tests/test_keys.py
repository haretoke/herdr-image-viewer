import unittest

from herdr_image_viewer import keys


class ConversationKeyTest(unittest.TestCase):
    def test_claude_payload_with_session_id_keys_the_history_by_that_session(self):
        payload = {"session_id": "3f2a9c1e-session", "tool_name": "Read"}

        key = keys.conversation_key(payload, socket_path="/run/herdr.sock", caller_pane="w1:p3")

        self.assertEqual(key, "claude:3f2a9c1e-session")


if __name__ == "__main__":
    unittest.main()
