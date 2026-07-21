from __future__ import annotations

from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings
from .database_url import validate_database_url


def _is_missing_database_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "does not exist" in message and "database" in message


def _database_name(parsed) -> str:
    name = (parsed.path or "").lstrip("/")
    return name


def _ensure_database_exists(url: str) -> None:
    connect_url = url
    if connect_url.startswith("postgresql+psycopg://"):
        connect_url = connect_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if connect_url.startswith("postgres+psycopg://"):
        connect_url = connect_url.replace("postgres+psycopg://", "postgres://", 1)

    parsed = urlparse(connect_url)
    db_name = _database_name(parsed)
    if not db_name:
        raise ValueError("DATABASE_URL must include a database name.")

    # If the database exists, this connection will succeed quickly.
    try:
        with psycopg.connect(connect_url, connect_timeout=5):
            return
    except Exception as exc:
        if not _is_missing_database_error(exc):
            raise ValueError(f"Failed to connect to database '{db_name}': {exc}") from exc

    admin_url = urlunparse(parsed._replace(path="/postgres"))
    try:
        with psycopg.connect(admin_url, connect_timeout=5) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                if cur.fetchone():
                    return
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    except Exception as exc:
        raise ValueError(
            f"Database '{db_name}' does not exist and could not be created. "
            "Create it manually or grant CREATEDB privileges."
        ) from exc


def _build_engine():
    url = validate_database_url(settings.database_url)
    _ensure_database_exists(url)
    # Prefer psycopg (v3) when a plain Postgres URL is provided.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return create_engine(
        url,
        future=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
    )


engine = _build_engine()


def _configure_engine_search_path() -> None:
    search_path = settings.db_search_path
    if not search_path:
        return

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        with dbapi_connection.cursor() as cur:
            cur.execute("select set_config('search_path', %s, false)", (search_path,))


_configure_engine_search_path()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def ensure_runtime_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "transactions" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("transactions")}
    with engine.begin() as conn:
        if "account_id" not in columns:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN account_id VARCHAR")
        if "dedup_key" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN dedup_key VARCHAR DEFAULT '' NOT NULL"
            )
        if "timestamp_precision" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN timestamp_precision VARCHAR "
                "DEFAULT 'unknown' NOT NULL"
            )
        if "source_statement_id" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN source_statement_id VARCHAR "
                "DEFAULT '' NOT NULL"
            )
        if "source_page_number" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN source_page_number INTEGER DEFAULT 0 NOT NULL"
            )
        if "source_row_index" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN source_row_index INTEGER DEFAULT 0 NOT NULL"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_transactions_account ON transactions(account_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_transactions_timestamp_precision "
            "ON transactions(timestamp_precision)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_transactions_source_ordering "
            "ON transactions(source_statement_id, source_page_number, source_row_index)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_account_dedup_key "
            "ON transactions(account_id, dedup_key) WHERE dedup_key <> ''"
        )
        if "balance_snapshots" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_balance_snapshots_statement_id "
                "ON balance_snapshots(statement_id)"
            )
        if "import_files" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_import_files_batch_id ON import_files(batch_id)"
            )
        if "statement_rows" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_statement_rows_statement_id "
                "ON statement_rows(statement_id)"
            )
        if "statements" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_statements_account_id ON statements(account_id)"
            )
        if "transfer_links" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfer_links_transaction_in_id "
                "ON transfer_links(transaction_in_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfer_links_transaction_out_id "
                "ON transfer_links(transaction_out_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfer_links_status ON transfer_links(status)"
            )
        if "transaction_statement_links" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transaction_statement_links_statement_row_id "
                "ON transaction_statement_links(statement_row_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transaction_statement_links_transaction_id "
                "ON transaction_statement_links(transaction_id)"
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
