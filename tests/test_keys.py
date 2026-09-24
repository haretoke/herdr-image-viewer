import unittest

from herdr_image_viewer import keys


class ConversationKeyTest(unittest.TestCase):
    def test_claude_payload_with_session_id_keys_the_history_by_that_session(self):
        payload = {"session_id": "3f2a9c1e-session", "tool_name": "Read"}

        key = keys.conversation_key(payload, socket_path="/run/herdr.sock", caller_pane="w1:p3")

        self.assertEqual(key, "claude:3f2a9c1e-session")

    def test_without_a_conversation_id_the_key_falls_back_to_the_caller_pane(self):
        payload = {"tool_name": "Read"}

        key = keys.conversation_key(payload, socket_path="/run/herdr.sock", caller_pane="w1:p3")

        self.assertEqual(key, "pane:/run/herdr.sock#w1:p3")

    def test_an_unusable_session_id_falls_back_to_the_caller_pane(self):
        for session_id in (None, 42, "", "a\nb", "a\x1b[31m", "x" * 257):
            with self.subTest(session_id=session_id):
                key = keys.conversation_key(
                    {"session_id": session_id}, socket_path="/run/herdr.sock", caller_pane="w1:p3"
                )

                self.assertEqual(key, "pane:/run/herdr.sock#w1:p3")

    def test_the_directory_name_is_a_hash_of_the_key(self):
        name = keys.directory_name("claude:../../etc/passwd")

        self.assertRegex(name, r"^[0-9a-f]{32}$")
        self.assertEqual(name, keys.directory_name("claude:../../etc/passwd"))
        self.assertNotEqual(name, keys.directory_name("claude:other"))


if __name__ == "__main__":
    unittest.main()
