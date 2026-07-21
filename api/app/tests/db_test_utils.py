from __future__ import annotations

import os
import re
import unittest
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from sqlalchemy.pool import NullPool

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}
SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_postgres_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_compare_url(url: str) -> str:
    return _normalize_postgres_url(url.strip().strip('"').strip("'"))


def get_test_engine() -> Engine:
    url = os.environ.get("TEST_DATABASE_URL", "").strip().strip('"').strip("'")
    if not url:
        raise unittest.SkipTest("TEST_DATABASE_URL is required for DB tests.")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    allow_local = _env_truthy("TEST_ALLOW_LOCAL_DB")
    if scheme.startswith("sqlite") or not scheme.startswith("postgres"):
        raise unittest.SkipTest("TEST_DATABASE_URL must be a Postgres URL.")
    if not host:
        raise unittest.SkipTest("TEST_DATABASE_URL must include a host.")
    if host in LOCAL_HOSTS and not allow_local:
        raise unittest.SkipTest(
            "TEST_DATABASE_URL points to local host; set TEST_ALLOW_LOCAL_DB=1 for local test-only runs."
        )

    runtime_url = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    if runtime_url:
        test_url_cmp = _normalize_compare_url(url)
        runtime_url_cmp = _normalize_compare_url(runtime_url)
        if test_url_cmp == runtime_url_cmp and not _env_truthy("TEST_ALLOW_SHARED_DB"):
            raise unittest.SkipTest(
                "Refusing to run DB tests against DATABASE_URL. "
                "Use a separate test database URL, or set TEST_ALLOW_SHARED_DB=1 for explicit override."
            )

    schema = os.environ.get("TEST_DATABASE_SCHEMA", "").strip()
    if not schema:
        raise unittest.SkipTest("Set TEST_DATABASE_SCHEMA to isolate external DB tests.")
    if not SCHEMA_RE.fullmatch(schema):
        raise unittest.SkipTest(
            "TEST_DATABASE_SCHEMA must be a simple SQL identifier "
            "(letters, digits, underscores, not starting with a digit)."
        )

    normalized = _normalize_postgres_url(url)
    admin_engine = create_engine(normalized, future=True, poolclass=NullPool)
    with admin_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    admin_engine.dispose()

    engine = create_engine(normalized, future=True, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        with dbapi_connection.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')

    return engine.execution_options(schema_translate_map={None: schema})
