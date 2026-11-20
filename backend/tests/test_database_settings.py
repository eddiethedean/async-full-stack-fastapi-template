import pytest
from sqlalchemy.engine import URL, make_url

from app.core.config import Settings


def test_compose_database_password_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "fixture/only%2F?@:+# test"
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("COMPOSE_POSTGRES_PASSWORD", password)
    settings = Settings(
        _env_file=None,
        ENABLE_TEST_ROUTES=False,
        FIRST_SUPERUSER="test@example.com",
        FIRST_SUPERUSER_PASSWORD="test-only-password",
        PROJECT_NAME="Test",
        SECRET_KEY="test-only-secret",
    )

    assert make_url(str(settings.DATABASE_URL)) == URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=password,
        host="db",
        port=5432,
        database="app",
    )


def test_explicit_database_url_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSE_POSTGRES_PASSWORD", "fixture/only")
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://external.example/asf_test_override?sslmode=require",
        FIRST_SUPERUSER="test@example.com",
        FIRST_SUPERUSER_PASSWORD="test-only-password",
        PROJECT_NAME="Test",
        SECRET_KEY="test-only-secret",
    )

    assert str(settings.DATABASE_URL) == (
        "postgresql+psycopg://external.example/asf_test_override?sslmode=require"
    )
