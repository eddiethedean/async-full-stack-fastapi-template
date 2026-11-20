"""Check migrations with data in historical schemas."""

import asyncio
import os
import subprocess
from collections.abc import Callable
from time import monotonic, sleep
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlmodel import select

from tests.database import test_database_url

BASELINE_REVISION = "fe56fa70289e"
OUTBOX_BASELINE_REVISION = "1c89be4a2070"
USER_ID = UUID("00000000-0000-4000-8000-000000000001")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000002")
EMAIL = "migration@example.com"
PASSWORD = "migration-test-password"


def upgrade_during_legacy_invitation(engine: Engine) -> None:
    user_id = uuid4()
    key = f"invite:{user_id}"
    with engine.connect() as writer, engine.connect() as observer:
        writer.execute(text("SET LOCAL statement_timeout = '10s'"))
        writer.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, is_active, '
                "is_superuser, invitation_pending) "
                "VALUES (:id, 'legacy-invite@example.com', 'unused', false, false, true)"
            ),
            {"id": user_id},
        )
        with subprocess.Popen(["alembic", "upgrade", "head"]) as migration:
            try:
                deadline = monotonic() + 10
                while not observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        """WHERE relation = '"user"'::regclass """
                        "AND mode = 'ShareRowExclusiveLock' AND NOT granted)"
                    )
                ):
                    assert migration.poll() is None and monotonic() < deadline
                    sleep(0.01)
                writer.execute(
                    text(
                        "INSERT INTO email_outbox "
                        "(id, kind, recipient, deduplication_key, payload, key_version, "
                        "attempts, created_at, available_at, expires_at) "
                        "VALUES (:id, 'invite', 'legacy-invite@example.com', :key, "
                        ":payload, 'development', 0, now(), now(), now() + interval '1 hour')"
                    ),
                    {"id": uuid4(), "key": key, "payload": b"legacy"},
                )
                writer.commit()
                assert migration.wait(timeout=10) == 0
            finally:
                if migration.poll() is None:
                    migration.terminate()

    with engine.begin() as conn:
        job = conn.execute(
            text(
                "SELECT user_id, failed_at, payload FROM email_outbox "
                "WHERE deduplication_key = :key"
            ),
            {"key": key},
        ).one()
        assert job == (user_id, None, b"legacy")
        conn.execute(
            text("DELETE FROM email_outbox WHERE deduplication_key = :key"),
            {"key": key},
        )


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

    offline_upgrade = subprocess.run(
        ["alembic", "upgrade", "--sql", f"{initial_revision}:{varchar_revision}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    with engine.connect() as conn:
        # The generated script contains its own BEGIN and COMMIT.
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql(offline_upgrade, execution_options={"no_parameters": True})
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == varchar_revision
        )
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
        upgrade_during_legacy_invitation(engine)
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
                elif kind == "password_reset":
                    # The legacy recovery was queued for a previous address.
                    assert user_id == USER_ID and failed_at is not None
                    assert payload is None and last_error == "account_email_changed"
                else:
                    assert user_id == USER_ID and failed_at is None
                    assert payload == b"legacy" and last_error is None
    finally:
        engine.dispose()
    asyncio.run(verify_upgraded_application())


def verify_uuid_downgrade() -> None:
    from sqlalchemy.engine import make_url

    database_url = test_database_url()
    integer_revision = "9c0a54914c78"
    uuid_revision = "d98dd8ec85a3"
    ownerless_item_id = UUID("00000000-0000-4000-8000-000000000003")
    migration_env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "FASTAPI_ENV": "development",
    }

    def migrate(direction: str, revision: str) -> None:
        subprocess.run(
            ["alembic", direction, revision],
            env=migration_env,
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
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO item (id, title) VALUES (:id, 'Ownerless UUID item')"
                ),
                {"id": ownerless_item_id},
            )

        failed = subprocess.run(
            ["alembic", "downgrade", integer_revision],
            env=migration_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert failed.returncode != 0
        assert f"Item {ownerless_item_id} has no owner" in failed.stderr
        assert "Assign a verified owner before retrying" in failed.stderr
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == uuid_revision
            )
            assert (
                conn.execute(
                    text("SELECT owner_id FROM item WHERE id = :id"),
                    {"id": ownerless_item_id},
                ).scalar_one()
                is None
            )
            conn.execute(
                text(
                    'UPDATE item SET owner_id = (SELECT id FROM "user" WHERE email = :email) '
                    "WHERE id = :id"
                ),
                {"email": email, "id": ownerless_item_id},
            )

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
            repaired_item, repaired_owner = conn.execute(
                text(
                    "SELECT id, owner_id FROM item WHERE title = 'Ownerless UUID item'"
                )
            ).one()
            assert repaired_owner == original_owner
            new_owner = conn.execute(
                insert_user, {"email": "after-rollback@example.com"}
            ).scalar_one()
            new_item = conn.execute(
                insert_item, {"title": "After rollback", "owner": new_owner}
            ).scalar_one()
            assert new_owner > original_owner
            assert new_item > max(original_item, repaired_item)
        migrate("downgrade", "base")
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify_uuid_downgrade()
    verify_populated_upgrade()
