from __future__ import annotations

import unittest

from app.cors_config import build_cors_options


class CorsConfigTests(unittest.TestCase):
    def test_wildcard_disables_credentials(self) -> None:
        origins, allow_credentials = build_cors_options("*")
        self.assertEqual(origins, ["*"])
        self.assertFalse(allow_credentials)

    def test_explicit_origins_allow_credentials(self) -> None:
        origins, allow_credentials = build_cors_options("https://a.test, https://b.test")
        self.assertEqual(origins, ["https://a.test", "https://b.test"])
        self.assertTrue(allow_credentials)

    def test_empty_input_defaults_to_wildcard(self) -> None:
        origins, allow_credentials = build_cors_options("")
        self.assertEqual(origins, ["*"])
        self.assertFalse(allow_credentials)


if __name__ == "__main__":
    unittest.main()
