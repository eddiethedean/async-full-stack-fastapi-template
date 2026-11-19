"""Check migrations with data in historical schemas."""

import asyncio
import os
import subprocess
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import select

from tests.database import test_database_url

BASELINE_REVISION = "fe56fa70289e"
USER_ID = UUID("00000000-0000-4000-8000-000000000001")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000002")
EMAIL = "migration@example.com"
PASSWORD = "migration-test-password"


async def verify_upgraded_application() -> None:
    from app.core.config import settings
    from app.core.db import create_session
    from app.email_outbox import EmailOutbox
    from app.main import app

    prefix = settings.API_V1_STR
    credentials = {"username": EMAIL, "password": PASSWORD}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as client,
    ):
        bearer_login = await client.post(
            f"{prefix}/login/access-token", data=credentials
        )
        bearer_login.raise_for_status()
        bearer = {"Authorization": f"Bearer {bearer_login.json()['access_token']}"}
        items = await client.get(f"{prefix}/items/", headers=bearer)
        items.raise_for_status()
        assert [item["id"] for item in items.json()["data"]] == [str(ITEM_ID)]

        browser = await client.post(
            f"{prefix}/auth/session",
            json=credentials,
            headers={"Origin": settings.auth_origins[0]},
        )
        browser.raise_for_status()
        restored = await client.get(f"{prefix}/auth/session")
        restored.raise_for_status()
        assert restored.json()["user"]["id"] == str(USER_ID)

        recovery_url = f"{prefix}/password-recovery"
        recovery = await client.post(recovery_url, json={"email": EMAIL})
        recovery.raise_for_status()
        limited = await client.post(recovery_url, json={"email": EMAIL})
        assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0

        async with create_session() as session:
            job = (
                await session.exec(
                    select(EmailOutbox).where(EmailOutbox.recipient == EMAIL)
                )
            ).one()
            assert job.kind == "password_reset" and job.payload


def verify_populated_upgrade() -> None:
    os.environ.update(
        DATABASE_URL=test_database_url(),
        FASTAPI_ENV="development",
        SMTP_HOST="mailpit",
        EMAILS_FROM_EMAIL="migration-check@example.com",
        RATE_LIMIT_RECOVERY_ACCOUNT="1",
        RATE_LIMIT_WINDOW_SECONDS="86400",
    )
    from app.core.config import settings
    from app.core.security import get_password_hash, verify_password

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
                    "email": EMAIL,
                    "password": get_password_hash(PASSWORD),
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
            assert email == EMAIL
            assert auth_version == 0
            assert verify_password(PASSWORD, password_hash)[0]
            owner, title = conn.execute(
                text("SELECT owner_id, title FROM item WHERE id = :id"), {"id": ITEM_ID}
            ).one()
            assert owner == USER_ID
            assert title == "Item from the previous release"
    finally:
        engine.dispose()
    asyncio.run(verify_upgraded_application())


def verify_uuid_downgrade() -> None:
    database_url = test_database_url()
    integer_revision = "9c0a54914c78"
    uuid_revision = "d98dd8ec85a3"

    def migrate(direction: str, revision: str) -> None:
        subprocess.run(
            ["alembic", direction, revision],
            env={
                **os.environ,
                "DATABASE_URL": database_url,
                "FASTAPI_ENV": "development",
            },
            check=True,
        )

    columns = text(
        "SELECT table_name, column_name, data_type, column_default, is_nullable "
        "FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND (table_name, column_name) IN "
        "(('user', 'id'), ('item', 'id'), ('item', 'owner_id')) "
        "ORDER BY table_name, column_name"
    )
    insert_user = text(
        'INSERT INTO "user" (email, hashed_password, is_active, is_superuser) '
        "VALUES (:email, 'fixture', true, false) RETURNING id"
    )
    insert_item = text(
        "INSERT INTO item (title, owner_id) VALUES (:title, :owner) RETURNING id"
    )
    email = "before-uuid@example.com"
    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    try:
        migrate("upgrade", integer_revision)
        with engine.begin() as conn:
            original_columns = conn.execute(columns).all()
            assert len(original_columns) == 3
            owner = conn.execute(insert_user, {"email": email}).scalar_one()
            conn.execute(insert_item, {"title": "Before UUIDs", "owner": owner})

        migrate("upgrade", uuid_revision)
        migrate("downgrade", integer_revision)
        with engine.begin() as conn:
            assert conn.execute(columns).all() == original_columns
            original_owner, original_item, item_owner = conn.execute(
                text(
                    'SELECT "user".id, item.id, item.owner_id FROM "user", item '
                    "WHERE email = :email AND title = 'Before UUIDs'"
                ),
                {"email": email},
            ).one()
            assert original_owner == item_owner
            new_owner = conn.execute(
                insert_user, {"email": "after-rollback@example.com"}
            ).scalar_one()
            new_item = conn.execute(
                insert_item, {"title": "After rollback", "owner": new_owner}
            ).scalar_one()
            assert new_owner > original_owner
            assert new_item > original_item
        migrate("downgrade", "base")
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify_uuid_downgrade()
    verify_populated_upgrade()
