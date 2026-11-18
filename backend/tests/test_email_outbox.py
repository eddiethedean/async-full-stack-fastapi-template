from datetime import timedelta
from logging import INFO, LogRecord
from smtplib import SMTPConnectError, SMTPRecipientsRefused
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import col, delete, select

from app.core.access_logs import SensitivePathFilter
from app.core.db import create_session
from app.email_outbox import (
    EmailKind,
    EmailOutbox,
    claim_email,
    complete_email,
    database_now,
    enqueue_email,
    get_cipher,
)
from app.email_worker import process_next_email

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def empty_outbox() -> None:
    async with create_session() as session, session.begin():
        await session.exec(delete(EmailOutbox))


async def test_rollback_discards_pending_email() -> None:
    key = uuid4().hex
    async with create_session() as session:
        enqueue_email(
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


async def test_lease_is_exclusive_and_stale_completion_is_ignored() -> None:
    async with create_session() as session, session.begin():
        enqueue_email(
            session,
            kind="invite",
            recipient="outbox@example.com",
            deduplication_key=uuid4().hex,
            token="test-invitation-credential",
        )
    async with create_session() as session_a, session_a.begin():
        first = await claim_email(session_a)
        assert first and first.payload and first.key_version
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
        second = await claim_email(session)
        assert second and second.id == first.id
        assert second.lease_id != first.lease_id
        assert not await complete_email(session, first)
        assert await complete_email(session, second)
        row = await session.get(EmailOutbox, first.id)
        assert row and row.delivered_at and row.payload is None


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
        enqueue_email(
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
        enqueue_email(
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


async def test_worker_drops_expired_credential() -> None:
    async with create_session() as session, session.begin():
        enqueue_email(
            session,
            kind="invite",
            recipient="expired@example.com",
            deduplication_key=uuid4().hex,
            token="expired-credential",
            expires_delta=timedelta(seconds=-1),
        )
    assert not await process_next_email()
    async with create_session() as session:
        row = (await session.exec(select(EmailOutbox))).one()
        assert row.payload is None
        assert row.failed_at and row.last_error == "expired"


async def test_worker_marks_permanent_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def smtp(**_data: str) -> None:
        raise SMTPRecipientsRefused({"unavailable@example.com": (550, b"unavailable")})

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        enqueue_email(
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
