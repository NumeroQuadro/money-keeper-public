from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
SEARCH_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def env_truthy(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(name, "")).strip().lower() in _TRUTHY_VALUES


def allow_local_postgres_for_tests(environ: Mapping[str, str] | None = None) -> bool:
    return env_truthy("TESTING", environ) and env_truthy("TEST_ALLOW_LOCAL_DB", environ)


def clean_database_url(value: str) -> str:
    return (value or "").strip().strip('"').strip("'")


def validate_database_search_path(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        return ""

    invalid = [part for part in parts if not SEARCH_PATH_SEGMENT_RE.fullmatch(part)]
    if invalid:
        raise ValueError(
            "DB_SEARCH_PATH must be a comma-separated list of SQL identifiers "
            "(for example: public,analytics)."
        )
    return ", ".join(parts)


def validate_database_url(
    url: str,
    *,
    allow_local_tests: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    cleaned = clean_database_url(url)
    if not cleaned:
        raise ValueError("DATABASE_URL is required and must point to external Postgres (Supabase).")

    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme.startswith("sqlite"):
        raise ValueError("SQLite is not allowed. Use an external Postgres URL (Supabase).")
    if not scheme.startswith("postgres"):
        raise ValueError("DATABASE_URL must be a Postgres URL (Supabase).")

    host = parsed.hostname
    if not host:
        raise ValueError("DATABASE_URL must include a host for external Postgres.")

    local_tests_allowed = (
        allow_local_postgres_for_tests(environ)
        if allow_local_tests is None
        else bool(allow_local_tests)
    )
    if host in LOCAL_DB_HOSTS and not local_tests_allowed:
        raise ValueError(
            "Local database hosts are not allowed outside tests. "
            "Use external Postgres (Supabase), or set TESTING=1 and TEST_ALLOW_LOCAL_DB=1 for test-only runs."
        )
    return cleaned
