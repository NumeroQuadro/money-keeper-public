from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.database_url import validate_database_search_path, validate_database_url


class ConfigGuardTests(unittest.TestCase):
    def test_rejects_local_host_outside_test_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://localhost/test_db",
                "TESTING": "0",
                "TEST_ALLOW_LOCAL_DB": "0",
            },
            clear=False,
        ):
            with self.assertRaises(Exception) as ctx:
                Settings()
            self.assertIn("Local database hosts are not allowed outside tests", str(ctx.exception))

    def test_allows_local_host_in_explicit_test_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://localhost/test_db",
                "TESTING": "1",
                "TEST_ALLOW_LOCAL_DB": "1",
            },
            clear=False,
        ):
            settings = Settings()
            self.assertEqual(settings.database_url, "postgresql://localhost/test_db")

    def test_still_rejects_sqlite_in_test_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite:///tmp/test.db",
                "TESTING": "1",
                "TEST_ALLOW_LOCAL_DB": "1",
            },
            clear=False,
        ):
            with self.assertRaises(Exception) as ctx:
                Settings()
            self.assertIn("SQLite is not allowed", str(ctx.exception))

    def test_shared_validation_matches_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTING": "0",
                "TEST_ALLOW_LOCAL_DB": "0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "Local database hosts are not allowed"):
                validate_database_url("postgresql://localhost/test_db")

    def test_accepts_and_normalizes_db_search_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://db.example.com/money_keeper",
                "DB_SEARCH_PATH": "public, analytics",
            },
            clear=False,
        ):
            settings = Settings()
            self.assertEqual(settings.db_search_path, "public, analytics")

    def test_rejects_invalid_db_search_path_segment(self) -> None:
        with self.assertRaisesRegex(ValueError, "comma-separated list of SQL identifiers"):
            validate_database_search_path("public, bad-schema")


if __name__ == "__main__":
    unittest.main()
