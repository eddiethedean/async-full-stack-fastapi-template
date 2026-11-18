import pytest
from pydantic import ValidationError
from sqlalchemy.engine import URL

from app.core.config import Settings
from tests.database import test_database_url as get_test_database_url


def test_guard_rejects_override_to_regular_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular_url = URL.create(
        "postgresql", host="review.invalid", database="example_app"
    )
    test_url = regular_url.set(
        database="asf_test_guard", query={"dbname": "example_app"}
    )
    monkeypatch.setenv("DATABASE_URL", str(regular_url))
    monkeypatch.setenv("TEST_DATABASE_URL", str(test_url))

    with pytest.raises(ValueError, match="effective test database name"):
        get_test_database_url()


def test_guard_compares_the_configured_application_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_url = URL.create(
        "postgresql", host="review.invalid", database="asf_test_guard"
    )
    app_url = test_url.set(database="example_app", query={"dbname": "asf_test_guard"})
    monkeypatch.setenv("DATABASE_URL", str(app_url))
    monkeypatch.setenv("TEST_DATABASE_URL", str(test_url))

    with pytest.raises(ValueError, match="must differ"):
        get_test_database_url()


def test_test_routes_require_a_disposable_database() -> None:
    test_url = URL.create(
        "postgresql",
        host="review.invalid",
        database="asf_test_guard",
        query={"dbname": "example_app"},
    )

    with pytest.raises(ValidationError, match="effective test database name"):
        Settings(
            _env_file=None,
            DATABASE_URL=str(test_url),
            ENABLE_TEST_ROUTES=True,
            FASTAPI_ENV="development",
            FIRST_SUPERUSER="test@example.com",
            FIRST_SUPERUSER_PASSWORD="test-only-password",
            PROJECT_NAME="Test",
            SECRET_KEY="test-only-secret",
        )
