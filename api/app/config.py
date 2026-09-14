from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .database_url import (
    LOCAL_DB_HOSTS,
    allow_local_postgres_for_tests,
    validate_database_search_path,
    validate_database_url,
)

__all__ = [
    "Settings",
    "settings",
    "LOCAL_DB_HOSTS",
    "allow_local_postgres_for_tests",
]

_ENV_FILE_CANDIDATES = (
    # Repo root when running from the monorepo checkout (e.g. `make test` does `cd api`).
    Path(__file__).resolve().parents[2] / ".env",
    # Fallback to current working directory for flexible local setups.
    ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE_CANDIDATES, extra="ignore")

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    api_admin_token: str = "change-me"
    api_bind_host: str = "0.0.0.0"
    api_bind_port: int = 8000
    cors_allowed_origins: str = "*"
    uploads_dir: str = "./data/uploads"
    db_search_path: str = Field(
        default="",
        validation_alias=AliasChoices("DB_SEARCH_PATH", "db_search_path"),
    )
    db_pool_size: int = Field(
        default=5,
        validation_alias=AliasChoices("DB_POOL_SIZE", "db_pool_size"),
    )
    db_max_overflow: int = Field(
        default=10,
        validation_alias=AliasChoices("DB_MAX_OVERFLOW", "db_max_overflow"),
    )
    db_pool_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("DB_POOL_TIMEOUT_SECONDS", "db_pool_timeout_seconds"),
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        return validate_database_url(value)

    @field_validator("db_search_path")
    @classmethod
    def validate_db_search_path(cls, value: str) -> str:
        return validate_database_search_path(value)

    def ensure_paths(self) -> None:
        Path(self.uploads_dir).mkdir(parents=True, exist_ok=True)


settings = Settings()
