import asyncio
from datetime import timedelta
from logging import INFO, LogRecord
from smtplib import SMTPConnectError, SMTPRecipientsRefused
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.access_logs import SensitivePathFilter
from app.core.config import settings
from app.core.db import create_session
from app.email_outbox import (
    MAX_ATTEMPTS,
    ClaimedEmail,
    EmailKind,
    EmailOutbox,
    claim_email,
    complete_email,
    database_now,
    enqueue_email,
    get_cipher,
    renew_email_lease,
)
from app.email_worker import process_next_email
from app.models import User
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def empty_outbox() -> None:
    async with create_session() as session, session.begin():
        await session.exec(delete(EmailOutbox))


async def test_rollback_discards_pending_email() -> None:
    key = uuid4().hex
    async with create_session() as session:
        await enqueue_email(
            session,
            kind="welcome",
            recipient="outbox@example.com",
            deduplication_key=key,
        )
        await session.flush()
        await session.rollback()
        row = (
            await session.exec(
                select(EmailOutbox).where(col(EmailOutbox.deduplication_key) == key)
            )
        ).first()
        assert row is None


@pytest.mark.parametrize("deleted_by_admin", [False, True])
async def test_account_deletion_cancels_queued_email(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    deleted_by_admin: bool,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    email, password = random_email(), random_lower_string()
    prefix = settings.API_V1_STR
    created = await client.post(
        f"{prefix}/users/",
        headers=superuser_token_headers,
        json={"email": email, "password": password},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    recovery = await client.post(f"{prefix}/password-recovery", json={"email": email})
    assert recovery.status_code == 200
    async with create_session() as session:
        pending = (await session.exec(select(EmailOutbox))).all()
        assert {job.kind for job in pending} == {"welcome", "password_reset"}
        assert all(
            str(job.user_id) == user_id and job.failed_at is None for job in pending
        )

    if deleted_by_admin:
        path, headers = user_id, superuser_token_headers
    else:
        path = "me"
        headers = await user_authentication_headers(
            client=client, email=email, password=password
        )
    deleted = await client.delete(f"{prefix}/users/{path}", headers=headers)
    assert deleted.status_code == 200

    with patch("app.email_worker.deliver_email") as deliver:
        assert not await process_next_email()
        deliver.assert_not_called()
    async with create_session() as session:
        assert (
            await session.exec(select(User).where(col(User.email) == email))
        ).first() is None
        cancelled = (await session.exec(select(EmailOutbox))).all()
        assert len(cancelled) == 2
        for job in cancelled:
            assert job.failed_at and job.last_error == "account_deleted"
            assert job.delivered_at is None and job.user_id is None
            assert job.payload is None and job.key_version is None


async def test_lease_is_exclusive_and_stale_completion_is_ignored() -> None:
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind="invite",
            recipient="outbox@example.com",
            deduplication_key=uuid4().hex,
            token="test-invitation-credential",
        )
    async with create_session() as session_a, session_a.begin():
        first = await claim_email(session_a)
        assert isinstance(first, ClaimedEmail) and first.payload and first.key_version
        assert (
            get_cipher(first.key_version).decrypt(first.payload)
            == b"test-invitation-credential"
        )
        async with create_session() as session_b, session_b.begin():
            assert await claim_email(session_b) is None
    async with create_session() as session, session.begin():
        row = await session.get(EmailOutbox, first.id)
        assert row
        row.lease_expires_at = await database_now(session) - timedelta(seconds=1)
    async with create_session() as session, session.begin():
        assert not await renew_email_lease(session, first)
    async with create_session() as session, session.begin():
        second = await claim_email(session)
        assert isinstance(second, ClaimedEmail) and second.id == first.id
        assert second.lease_id != first.lease_id
        assert not await renew_email_lease(session, first)
        assert not await complete_email(session, first)
        assert await complete_email(session, second)
        row = await session.get(EmailOutbox, first.id)
        assert row and row.delivered_at and row.payload is None


async def test_worker_renews_lease_during_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polled = asyncio.Event()
    renewed = asyncio.Event()
    finish_delivery = Event()

    def report_progress() -> None:
        if polled.is_set():
            renewed.set()
        else:
            polled.set()

    def smtp(**_data: str) -> None:
        assert finish_delivery.wait(timeout=10)

    async def track_renewal(session: AsyncSession, claimed: ClaimedEmail) -> bool:
        row = await session.get(EmailOutbox, claimed.id)
        assert row and row.lease_expires_at
        previous_expiry = row.lease_expires_at
        assert await renew_email_lease(session, claimed)
        await session.refresh(row)
        assert row.lease_expires_at and row.lease_expires_at > previous_expiry
        return True

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    monkeypatch.setattr("app.email_worker.renew_email_lease", track_renewal)
    monkeypatch.setattr("app.email_worker.LEASE_RENEW_INTERVAL", 0.01)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind="welcome",
            recipient="slow-delivery@example.com",
            deduplication_key=uuid4().hex,
        )

    delivery = asyncio.create_task(process_next_email(on_progress=report_progress))
    try:
        await asyncio.wait_for(renewed.wait(), timeout=5)
        assert polled.is_set() and not delivery.done()
    finally:
        finish_delivery.set()
        assert await delivery

    async with create_session() as session:
        row = (await session.exec(select(EmailOutbox))).one()
        assert row.delivered_at and row.lease_id is None


async def test_worker_retries_transient_smtp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[str] = []

    def smtp(*, message_id: str, **kwargs: str) -> None:
        assert kwargs["email_to"] == "retry@example.com"
        delivered.append(message_id)
        if len(delivered) == 1:
            raise SMTPConnectError(421, "Test SMTP outage")

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    key = uuid4().hex
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind="welcome",
            recipient="retry@example.com",
            deduplication_key=key,
        )

    assert await process_next_email()
    async with create_session() as session, session.begin():
        row = (
            await session.exec(
                select(EmailOutbox).where(col(EmailOutbox.deduplication_key) == key)
            )
        ).one()
        assert row.last_error == "smtp_rejected"
        assert row.available_at > await database_now(session)
        assert row.delivered_at is None
        row.available_at = await database_now(session) - timedelta(seconds=1)

    assert await process_next_email()
    assert len(delivered) == 2 and delivered[0] == delivered[1]


@pytest.mark.parametrize("kind", ["password_reset", "invite"])
async def test_worker_renders_credential_before_delivery(
    monkeypatch: pytest.MonkeyPatch, kind: EmailKind
) -> None:
    email = "delivery@example.com"
    token = "temporary-credential"
    calls: list[dict[str, str]] = []

    def smtp(**data: str) -> None:
        calls.append(data)

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind=kind,
            recipient=email,
            deduplication_key=uuid4().hex,
            token=token,
        )
    assert await process_next_email()
    assert calls[0]["email_to"] == email
    assert token in calls[0]["html_content"]
    assert not await process_next_email()


@pytest.mark.parametrize(
    ("expires_delta", "attempts", "last_error"),
    [
        (timedelta(seconds=-1), 0, "expired"),
        (timedelta(hours=1), MAX_ATTEMPTS, "lease_exhausted"),
    ],
)
async def test_worker_keeps_polling_after_retiring_a_job(
    monkeypatch: pytest.MonkeyPatch,
    expires_delta: timedelta,
    attempts: int,
    last_error: str,
) -> None:
    delivered: list[str] = []

    def smtp(*, email_to: str, **_data: str) -> None:
        delivered.append(email_to)

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind="invite",
            recipient="retired@example.com",
            deduplication_key=uuid4().hex,
            token="temporary-credential",
            expires_delta=expires_delta,
        )
        await session.flush()
        retired = (await session.exec(select(EmailOutbox))).one()
        retired.available_at -= timedelta(seconds=1)
        retired.attempts = attempts
        retired_id = retired.id
        await enqueue_email(
            session,
            kind="welcome",
            recipient="ready@example.com",
            deduplication_key=uuid4().hex,
        )

    assert await process_next_email()
    assert not delivered
    async with create_session() as session:
        row = await session.get(EmailOutbox, retired_id)
        assert row and row.payload is None and row.key_version is None
        assert row.failed_at and row.last_error == last_error

    assert await process_next_email()
    assert delivered == ["ready@example.com"]
    assert not await process_next_email()


async def test_worker_marks_permanent_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def smtp(**_data: str) -> None:
        raise SMTPRecipientsRefused({"unavailable@example.com": (550, b"unavailable")})

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            kind="invite",
            recipient="unavailable@example.com",
            deduplication_key=uuid4().hex,
            token="temporary-credential",
        )
    assert await process_next_email()
    async with create_session() as session:
        row = (await session.exec(select(EmailOutbox))).one()
        assert row.payload is None
        assert row.failed_at and row.last_error == "smtp_recipient"


@pytest.mark.parametrize("tls,ssl", [(True, False), (False, True), (False, False)])
async def test_admin_mail_probe_uses_configured_transport(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tls: bool,
    ssl: bool,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 1025)
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    monkeypatch.setattr(settings, "SMTP_USER", "test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-only")
    monkeypatch.setattr(settings, "SMTP_TLS", tls)
    monkeypatch.setattr(settings, "SMTP_SSL", ssl)
    with (
        patch("app.utils.SMTP") as smtp,
        patch("app.utils.SMTP_SSL") as smtp_ssl,
    ):
        connection = (smtp_ssl if ssl else smtp).return_value
        connection.send_message.return_value = {}
        response = await client.post(
            f"{settings.API_V1_STR}/utils/test-email/",
            headers=superuser_token_headers,
            params={"email_to": "recipient@example.com"},
        )
        assert response.status_code == 201
        assert connection.starttls.call_count == int(tls and not ssl)
        connection.login.assert_called_once_with("test", "test-only")
        message = connection.send_message.call_args.args[0]
        assert message["To"] == "recipient@example.com"


def test_legacy_recovery_access_logs_remove_sensitive_targets() -> None:
    for original, expected in (
        (
            "/api/v1/password-recovery/user@example.com",
            "/api/v1/password-recovery/{email}",
        ),
        ("/reset-password?token=example", "/reset-password"),
    ):
        record = LogRecord(
            "uvicorn.access",
            INFO,
            "test",
            0,
            "%s %s %s %s %s",
            ("local", "GET", original, "1.1", 200),
            None,
        )
        assert SensitivePathFilter().filter(record)
        assert record.args == ("local", "GET", expected, "1.1", 200)
