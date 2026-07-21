import unittest

from bot import main as bot_main


class StatusFormatTests(unittest.TestCase):
    def test_processed_maps_to_parsed(self) -> None:
        text = bot_main._format_file_result(
            batch_id="batch-1",
            file_info={
                "id": "file-1",
                "file_name": "statement.pdf",
                "status": "processed",
                "file_hash": "abc",
            },
        )
        self.assertIn("Status: ✅ parsed", text)
        self.assertIn("added to the database", text)

    def test_processing_includes_parsing_action(self) -> None:
        text = bot_main._format_file_result(
            batch_id="batch-2",
            file_info={
                "id": "file-2",
                "file_name": "statement.pdf",
                "status": "processing",
            },
        )
        self.assertIn("Status: ⏳ processing", text)
        self.assertIn("Parsing PDF", text)

    def test_failed_not_pdf_message(self) -> None:
        text = bot_main._format_file_result(
            batch_id="batch-3",
            file_info={
                "id": "file-3",
                "file_name": "statement.txt",
                "status": "failed",
                "error_message": "Not a PDF",
            },
        )
        self.assertIn("Status: ❌ failed", text)
        self.assertIn("Rejected; not queued.", text)

    def test_failed_parsing_message(self) -> None:
        text = bot_main._format_file_result(
            batch_id="batch-4",
            file_info={
                "id": "file-4",
                "file_name": "statement.pdf",
                "status": "failed",
                "error_message": "Parsing failed: bad format",
            },
        )
        self.assertIn("Status: ❌ failed", text)
        self.assertIn("Import failed during parsing.", text)

    def test_exception_keyboard_includes_quick_actions(self) -> None:
        keyboard = bot_main._build_queue_keyboard(
            kind="exception",
            item_id="exc-1",
            exception={
                "entity_type": "transaction",
                "exception_type": "suspected_duplicate",
                "payload": {"suggested_category": "Food"},
            },
        )
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("queue:exc:ac:exc-1", callbacks)
        self.assertIn("queue:exc:dup:exc-1", callbacks)
        self.assertIn("queue:exception:resolve:exc-1", callbacks)

    def test_exception_text_includes_suggested_category_and_duplicate_hint(self) -> None:
        text = bot_main._format_exception_queue_item(
            {
                "exception_type": "suspected_duplicate",
                "severity": "medium",
                "status": "open",
                "entity_type": "transaction",
                "entity_id": "tx-1",
                "payload": {"suggested_category": "Food"},
            }
        )
        self.assertIn("Suggested category: Food", text)
        self.assertIn("Duplicate candidate: yes", text)

    def test_api_base_url_validation(self) -> None:
        self.assertEqual(
            bot_main._sanitize_api_base_url("https://example.com/api/"),
            "https://example.com/api",
        )
        with self.assertRaisesRegex(ValueError, "required"):
            bot_main._sanitize_api_base_url("")
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            bot_main._sanitize_api_base_url("ftp://example.com/api")
        with self.assertRaisesRegex(ValueError, "must include a host"):
            bot_main._sanitize_api_base_url("http:///api")

    def test_retryable_status_mapping(self) -> None:
        self.assertTrue(bot_main._is_retryable_status(503))
        self.assertTrue(bot_main._is_retryable_status(429))
        self.assertFalse(bot_main._is_retryable_status(404))


if __name__ == "__main__":
    unittest.main()
