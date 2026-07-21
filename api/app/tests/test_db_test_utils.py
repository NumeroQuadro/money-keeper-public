from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.tests.db_test_utils import get_test_engine


class TestDbTestUtilsGuards(unittest.TestCase):
    def test_shared_runtime_url_is_blocked_without_explicit_override(self) -> None:
        env = {
            "DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_DATABASE_SCHEMA": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(unittest.SkipTest):
                get_test_engine()

    def test_shared_runtime_url_allows_progress_with_explicit_override(self) -> None:
        env = {
            "DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_ALLOW_SHARED_DB": "1",
            "TEST_DATABASE_SCHEMA": "",
        }
        with patch.dict(os.environ, env, clear=False):
            # With override set, the next guard should require schema.
            with self.assertRaisesRegex(unittest.SkipTest, "TEST_DATABASE_SCHEMA"):
                get_test_engine()

    def test_invalid_schema_identifier_is_rejected(self) -> None:
        env = {
            "DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_DATABASE_URL": "postgresql://user:pass@db.example.com:5432/appdb",
            "TEST_ALLOW_SHARED_DB": "1",
            "TEST_DATABASE_SCHEMA": "bad-schema",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(unittest.SkipTest, "simple SQL identifier"):
                get_test_engine()


if __name__ == "__main__":
    unittest.main()
