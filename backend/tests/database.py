"""Choose a disposable database before loading any application settings."""

import os
import subprocess
import sys
from pathlib import Path

from pydantic import PostgresDsn, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.database_url import postgres_database_name, require_test_database


class ApplicationDatabase(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    DATABASE_URL: PostgresDsn | None = None


def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise ValueError("Set TEST_DATABASE_URL to a disposable PostgreSQL database.")
    try:
        database = PostgresDsn(url)
        app_database = ApplicationDatabase().DATABASE_URL
    except ValidationError:
        raise ValueError(
            "Check TEST_DATABASE_URL and DATABASE_URL configuration."
        ) from None

    test_name = require_test_database(database)
    if app_database and test_name == postgres_database_name(app_database):
        raise ValueError("The test database must differ from the application database.")
    return url


if __name__ == "__main__":
    try:
        url = test_database_url()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    result = subprocess.run(
        ["alembic", *(sys.argv[1:] or ["upgrade", "head"])],
        env={**os.environ, "DATABASE_URL": url, "FASTAPI_ENV": "development"},
        check=False,
    )
    raise SystemExit(result.returncode)
