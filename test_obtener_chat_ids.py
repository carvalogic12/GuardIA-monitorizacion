import io
import unittest

from obtener_chat_ids import clean_name, printable_chats, print_table


class ChatIdScriptTests(unittest.TestCase):
    def test_printable_chats_normalizes_names(self):
        chats = [
            {"id": -100123, "type": "supergroup", "title": "Alertas\x1b[31m"},
            {"id": 42, "type": "private", "first_name": "Javier"},
        ]
        self.assertEqual(printable_chats(chats), [
            {"chat_id": -100123, "type": "supergroup", "name": "Alertas[31m"},
            {"chat_id": 42, "type": "private", "name": "Javier"},
        ])

    def test_clean_name_has_fallback(self):
        self.assertEqual(clean_name({}), "(sin nombre)")

    def test_table_lists_ids_and_empty_instructions(self):
        output = io.StringIO()
        print_table([{"id": -100123, "type": "group", "title": "Operaciones"}], output)
        self.assertIn("-100123", output.getvalue())
        self.assertIn("Operaciones", output.getvalue())

        output = io.StringIO()
        print_table([], output)
        self.assertIn("/start@NombreDeTuBot", output.getvalue())


if __name__ == "__main__":
    unittest.main()
