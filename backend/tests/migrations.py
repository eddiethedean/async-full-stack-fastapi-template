"""Check an upgrade from the last release with data in the old schema."""

import os
import subprocess
from uuid import UUID

from sqlalchemy import create_engine, text

from tests.database import test_database_url

BASELINE_REVISION = "fe56fa70289e"
USER_ID = UUID("00000000-0000-4000-8000-000000000001")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000002")


def verify_populated_upgrade() -> None:
    os.environ.update(DATABASE_URL=test_database_url(), FASTAPI_ENV="development")
    from app.core.config import settings
    from app.core.security import get_password_hash, verify_password
    from app.models import SQLModel

    def migrate(*args: str) -> None:
        subprocess.run(["alembic", *args], check=True)

    engine = create_engine(str(settings.DATABASE_URL))
    try:
        migrate("upgrade", BASELINE_REVISION)
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO "user" (id, email, hashed_password, is_active, is_superuser) '
                    "VALUES (:id, :email, :password, true, false)"
                ),
                {
                    "id": USER_ID,
                    "email": "migration@example.com",
                    "password": get_password_hash("migration-test-password"),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO item (id, title, owner_id) VALUES (:id, :title, :owner)"
                ),
                {
                    "id": ITEM_ID,
                    "title": "Item from the previous release",
                    "owner": USER_ID,
                },
            )
        migrate("upgrade", "head")
        migrate("check")
        with engine.connect() as conn:
            email, password_hash, auth_version = conn.execute(
                text(
                    'SELECT email, hashed_password, auth_version FROM "user" WHERE id = :id'
                ),
                {"id": USER_ID},
            ).one()
            assert email == "migration@example.com"
            assert auth_version == 0
            assert verify_password("migration-test-password", password_hash)[0]
            owner, title = conn.execute(
                text("SELECT owner_id, title FROM item WHERE id = :id"), {"id": ITEM_ID}
            ).one()
            assert owner == USER_ID
            assert title == "Item from the previous release"
            for table in SQLModel.metadata.sorted_tables:
                conn.execute(table.select().limit(1))
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify_populated_upgrade()
