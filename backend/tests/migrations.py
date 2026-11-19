"""Check an upgrade from the last release with data in the old schema."""

import asyncio
import os
import subprocess
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlmodel import select

from tests.database import test_database_url

BASELINE_REVISION = "fe56fa70289e"
OUTBOX_BASELINE_REVISION = "1c89be4a2070"
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
        migrate("upgrade", OUTBOX_BASELINE_REVISION)
        legacy_reset_digest = "a" * 64
        legacy_jobs = {
            f"welcome:{USER_ID}": "welcome",
            f"reset:{legacy_reset_digest}": "password_reset",
            f"invite:{uuid4()}": "invite",
        }
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO accountactiontoken "
                    "(id, user_id, purpose, digest, auth_version, created_at, expires_at) "
                    "VALUES (:id, :user_id, 'password_reset', :digest, 0, now(), "
                    "now() + interval '1 hour')"
                ),
                {"id": uuid4(), "user_id": USER_ID, "digest": legacy_reset_digest},
            )
            conn.execute(
                text(
                    "INSERT INTO email_outbox "
                    "(id, kind, recipient, deduplication_key, payload, key_version, "
                    "attempts, created_at, available_at, expires_at) "
                    "VALUES (:id, :kind, 'legacy-recipient@example.com', :key, "
                    ":payload, 'development', 0, now(), now(), now() + interval '1 hour')"
                ),
                [
                    {"id": uuid4(), "kind": kind, "key": key, "payload": b"legacy"}
                    for key, kind in legacy_jobs.items()
                ],
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
            jobs = conn.execute(
                text(
                    "SELECT kind, user_id, failed_at, payload, last_error "
                    "FROM email_outbox"
                )
            ).all()
            assert len(jobs) == len(legacy_jobs)
            for kind, user_id, failed_at, payload, last_error in jobs:
                if kind == "invite":
                    assert user_id is None and failed_at is not None
                    assert payload is None and last_error == "account_unavailable"
                else:
                    assert user_id == USER_ID and failed_at is None
                    assert payload == b"legacy" and last_error is None
    finally:
        engine.dispose()
    asyncio.run(verify_upgraded_application())


if __name__ == "__main__":
    verify_populated_upgrade()
