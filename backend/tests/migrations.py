"""Check an upgrade from the last release with data in the old schema."""

import asyncio
import os
import subprocess
from collections.abc import Callable
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
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


def verify_varchar_preflight(
    engine: Engine, migrate: Callable[[str, str], None]
) -> None:
    initial_revision = "e2412789c190"
    varchar_revision = "9c0a54914c78"
    fits = "界" * 255
    exceeds_limit = "界" * 256
    insert_user = text(
        'INSERT INTO "user" (email, hashed_password, full_name, is_active, is_superuser) '
        "VALUES (:email, 'fixture', :name, true, false) RETURNING id"
    )
    full_name = text('SELECT full_name FROM "user" WHERE id = :id')

    migrate("upgrade", initial_revision)
    with engine.begin() as conn:
        owner = conn.execute(
            insert_user, {"email": "boundary@example.com", "name": fits}
        ).scalar_one()
        oversized = conn.execute(
            insert_user, {"email": "oversized@example.com", "name": exceeds_limit}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO item (title, description, owner_id) VALUES (:name, :name, :owner)"
            ),
            {"name": fits, "owner": owner},
        )

    failed = subprocess.run(
        ["alembic", "upgrade", varchar_revision],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    assert f"user.full_name for row ID {oversized} with 256 characters" in failed.stderr
    assert "Preserve the original data" in failed.stderr
    assert exceeds_limit not in failed.stderr
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == initial_revision
        )
        assert conn.execute(full_name, {"id": oversized}).scalar_one() == exceeds_limit
        conn.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": oversized})

    migrate("upgrade", varchar_revision)
    with engine.connect() as conn:
        assert conn.execute(full_name, {"id": owner}).scalar_one() == fits
        assert conn.execute(text("SELECT title, description FROM item")).one() == (
            fits,
            fits,
        )
    migrate("downgrade", "base")


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
        verify_varchar_preflight(engine, migrate)
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


if __name__ == "__main__":
    verify_populated_upgrade()
