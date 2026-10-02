import io
import json
import os
import pty
import unittest
from unittest.mock import patch

from seleccionar_chat import get_chats, select_chat, select_from_terminal


class Terminal:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.output = ""

    def readline(self):
        return next(self.answers, "")

    def write(self, text):
        self.output += text

    def flush(self):
        pass


class ChatTests(unittest.TestCase):
    @patch("seleccionar_chat.get_chats")
    def test_selection_on_real_non_seekable_terminal(self, query):
        query.return_value = [{"id": -5410674616, "type": "group", "title": "Alertas"}]
        master, slave = pty.openpty()
        try:
            os.write(master, b"\n1\n")
            self.assertEqual(select_from_terminal("test-token", os.ttyname(slave)), "-5410674616")
        finally:
            os.close(slave)
            os.close(master)

    @patch("seleccionar_chat.urllib.request.urlopen")
    def test_discovers_unique_chats_without_offset(self, request):
        group = {"id": -100123, "type": "supergroup", "title": "Alertas"}
        private = {"id": 123, "type": "private", "first_name": "Usuario"}
        request.return_value = io.BytesIO(json.dumps({"ok": True, "result": [
            {"message": {"chat": group}}, {"my_chat_member": {"chat": group}},
            {"message": {"chat": private}}]}).encode())
        self.assertEqual(get_chats("test-token"), [group, private])
        self.assertTrue(request.call_args.args[0].full_url.endswith("/getUpdates"))

    @patch("seleccionar_chat.get_chats")
    def test_retry_and_select_group(self, query):
        query.side_effect = [[], [{"id": -100123, "type": "supergroup", "title": "Alertas"}]]
        terminal = Terminal(["\n", "r\n", "1\n"])
        self.assertEqual(select_chat("test-token", terminal), "-100123")
        self.assertIn("Alertas", terminal.output)

    @patch("seleccionar_chat.get_chats", side_effect=RuntimeError("No disponible"))
    def test_manual_fallback(self, query):
        self.assertEqual(select_chat("test-token", Terminal(["\n", "m\n", "-123\n"])), "-123")

    @patch("seleccionar_chat.get_chats", return_value=[])
    def test_cancel(self, query):
        with self.assertRaisesRegex(RuntimeError, "cancelada"):
            select_chat("test-token", Terminal(["\n", "q\n"]))


if __name__ == "__main__":
    unittest.main()
