import asyncio
from collections.abc import Callable
from datetime import timedelta
from email.headerregistry import Address
from email.parser import BytesParser
from email.policy import default
from functools import partial
from logging import INFO, LogRecord
from smtplib import SMTP, SMTPConnectError, SMTPRecipientsRefused
from threading import Event
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import email_worker
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
from app.email_worker import deliver_with_lease, process_next_email
from app.models import User
from app.utils import EmailData
from tests.utils.user import create_random_user, user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def empty_outbox() -> None:
    async with create_session() as session, session.begin():
        await session.exec(delete(EmailOutbox))


@pytest.fixture
async def email_owner_id(db: AsyncSession) -> UUID:
    return (await create_random_user(db)).id


async def test_rollback_discards_pending_email(email_owner_id: UUID) -> None:
    key = uuid4().hex
    async with create_session() as session:
        await enqueue_email(
            session,
            user_id=email_owner_id,
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


@pytest.mark.parametrize(
    ("deleted_by_admin", "claimed_before_deletion"),
    [(False, False), (True, False), (False, True)],
    ids=["self-delete", "admin-delete", "claimed-before-delete"],
)
async def test_account_deletion_cancels_queued_email(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    deleted_by_admin: bool,
    claimed_before_deletion: bool,
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
    async with create_session() as session, session.begin():
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

    async def delete_account() -> None:
        deleted = await client.delete(f"{prefix}/users/{path}", headers=headers)
        assert deleted.status_code == 200

    async def delete_before_send(
        session: AsyncSession,
        claimed: ClaimedEmail,
        on_progress: Callable[[], None] | None,
    ) -> bool:
        await delete_account()
        return await deliver_with_lease(session, claimed, on_progress)

    with patch("app.email_worker.send_email") as send:
        if claimed_before_deletion:
            monkeypatch.setattr(
                "app.email_worker.deliver_with_lease", delete_before_send
            )
            assert await process_next_email()
        else:
            await delete_account()
        assert not await process_next_email()
        send.assert_not_called()
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
            assert job.lease_id is None and job.lease_expires_at is None


@pytest.mark.parametrize("claimed_before_deactivation", [False, True])
async def test_deactivation_cancels_pending_welcome(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    claimed_before_deactivation: bool,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    prefix = settings.API_V1_STR
    created = await client.post(
        f"{prefix}/users/",
        headers=superuser_token_headers,
        json={"email": random_email(), "password": random_lower_string()},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    async with create_session() as session:
        job = (await session.exec(select(EmailOutbox))).one()
        assert str(job.user_id) == user_id and job.kind == "welcome"
        assert job.failed_at is None

    async def deactivate() -> None:
        result = await client.patch(
            f"{prefix}/users/{user_id}",
            headers=superuser_token_headers,
            json={"is_active": False},
        )
        assert result.status_code == 200

    async def deactivate_before_send(
        session: AsyncSession,
        claimed: ClaimedEmail,
        on_progress: Callable[[], None] | None,
    ) -> bool:
        await deactivate()
        return await deliver_with_lease(session, claimed, on_progress)

    with patch.object(email_worker, "send_email") as send:
        if claimed_before_deactivation:
            monkeypatch.setattr(
                email_worker, "deliver_with_lease", deactivate_before_send
            )
            assert await process_next_email()
        else:
            await deactivate()
        send.assert_not_called()
        assert not await process_next_email()

    async with create_session() as session:
        job = (await session.exec(select(EmailOutbox))).one()
        assert job.failed_at is not None and job.last_error == "account_deactivated"
        assert job.delivered_at is None
        assert job.payload is None and job.key_version is None
        assert job.lease_id is None and job.lease_expires_at is None


@pytest.mark.parametrize("claimed_before_change", [False, True])
async def test_email_change_cancels_queued_recovery(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    claimed_before_change: bool,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    old_email, new_email = random_email(), random_email()
    prefix = settings.API_V1_STR
    signup = await client.post(
        f"{prefix}/users/signup",
        json={"email": old_email, "password": random_lower_string()},
    )
    assert signup.status_code == 200
    recovery_url = f"{prefix}/password-recovery"
    assert (
        await client.post(recovery_url, json={"email": old_email})
    ).status_code == 200

    async def change_email() -> None:
        result = await client.patch(
            f"{prefix}/users/{signup.json()['id']}",
            headers=superuser_token_headers,
            json={"email": new_email},
        )
        assert result.status_code == 200

    async def change_before_send(
        session: AsyncSession,
        claimed: ClaimedEmail,
        on_progress: Callable[[], None] | None,
    ) -> bool:
        await change_email()
        return await deliver_with_lease(session, claimed, on_progress)

    with patch.object(email_worker, "send_email") as send:
        if claimed_before_change:
            with monkeypatch.context() as patch_worker:
                patch_worker.setattr(
                    email_worker, "deliver_with_lease", change_before_send
                )
                assert await process_next_email()
        else:
            await change_email()
        assert not await process_next_email()
        send.assert_not_called()

        async with create_session() as session:
            job = (await session.exec(select(EmailOutbox))).one()
            assert job.recipient == old_email
            assert (
                job.failed_at is not None and job.last_error == "account_email_changed"
            )
            assert job.delivered_at is None
            assert job.payload is None and job.key_version is None
            assert job.lease_id is None and job.lease_expires_at is None

        recovery = await client.post(recovery_url, json={"email": new_email})
        assert recovery.status_code == 200
        assert await process_next_email()
        send.assert_called_once()
        assert send.call_args.kwargs["email_to"] == new_email


@pytest.mark.parametrize(
    ("change", "claimed_before_change"),
    [("reset", False), ("self", False), ("admin", True), ("deactivate", False)],
    ids=["password-reset", "self-password", "claimed-admin-password", "deactivate"],
)
async def test_credential_invalidation_cancels_pending_recovery(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    claimed_before_change: bool,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    email, password = random_email(), random_lower_string()
    prefix = settings.API_V1_STR
    signup = await client.post(
        f"{prefix}/users/signup", json={"email": email, "password": password}
    )
    assert signup.status_code == 200
    user_id = signup.json()["id"]
    recovery_url = f"{prefix}/password-recovery"
    assert (await client.post(recovery_url, json={"email": email})).status_code == 200

    new_password = random_lower_string()
    token = ""
    if change == "reset":
        async with create_session() as session:
            job = (await session.exec(select(EmailOutbox))).one()
            assert job.payload is not None and job.key_version is not None
            token = get_cipher(job.key_version).decrypt(job.payload).decode()
        with patch.object(email_worker, "send_email") as send:
            assert await process_next_email()
            send.assert_called_once()
        assert (
            await client.post(recovery_url, json={"email": email})
        ).status_code == 200

    async def change_credentials() -> None:
        if change == "reset":
            result = await client.post(
                f"{prefix}/reset-password/",
                json={"token": token, "new_password": new_password},
            )
        elif change == "self":
            headers = await user_authentication_headers(
                client=client, email=email, password=password
            )
            result = await client.patch(
                f"{prefix}/users/me/password",
                headers=headers,
                json={"current_password": password, "new_password": new_password},
            )
        else:
            result = await client.patch(
                f"{prefix}/users/{user_id}",
                headers=superuser_token_headers,
                json=(
                    {"is_active": False}
                    if change == "deactivate"
                    else {"password": new_password}
                ),
            )
        assert result.status_code == 200

    async def change_before_send(
        session: AsyncSession,
        claimed: ClaimedEmail,
        on_progress: Callable[[], None] | None,
    ) -> bool:
        await change_credentials()
        return await deliver_with_lease(session, claimed, on_progress)

    with patch.object(email_worker, "send_email") as send:
        if claimed_before_change:
            with monkeypatch.context() as patch_worker:
                patch_worker.setattr(
                    email_worker, "deliver_with_lease", change_before_send
                )
                assert await process_next_email()
        else:
            await change_credentials()
        send.assert_not_called()
        assert not await process_next_email()

        async with create_session() as session:
            jobs = (await session.exec(select(EmailOutbox))).all()
            assert len(jobs) == (2 if change == "reset" else 1)
            job = next(job for job in jobs if job.delivered_at is None)
            assert job.failed_at is not None
            assert job.last_error == "account_credentials_changed"
            assert job.payload is None and job.key_version is None
            assert job.lease_id is None and job.lease_expires_at is None

        if change != "deactivate":
            assert (
                await client.post(recovery_url, json={"email": email})
            ).status_code == 200
            assert await process_next_email()
            send.assert_called_once()


async def test_account_deletion_waits_for_smtp_and_preserves_delivery(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    email = random_email()
    users_url = f"{settings.API_V1_STR}/users/"
    created = await client.post(
        users_url,
        headers=superuser_token_headers,
        json={"email": email, "password": random_lower_string()},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    before_smtp = asyncio.Event()
    start_smtp = asyncio.Event()
    deletion_waiting = asyncio.Event()
    delivery_progress = asyncio.Event()
    progress_ticks = 0
    dispatch = email_worker.run_in_threadpool

    def report_progress() -> None:
        nonlocal progress_ticks
        if deletion_waiting.is_set():
            progress_ticks += 1
            if progress_ticks == 2:
                delivery_progress.set()

    async def pause_before_smtp(
        function: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        if function is email_worker.send_email:
            before_smtp.set()
            await start_smtp.wait()
        return await dispatch(function, *args, **kwargs)

    monkeypatch.setattr(email_worker, "run_in_threadpool", pause_before_smtp)
    monkeypatch.setattr(email_worker, "LEASE_RENEW_INTERVAL", 0.01)
    monkeypatch.setattr("app.email_outbox.LEASE_DURATION", timedelta(seconds=1))
    with patch.object(email_worker, "send_email") as send:
        delivery = asyncio.create_task(process_next_email(on_progress=report_progress))
        await asyncio.wait_for(before_smtp.wait(), timeout=5)
        deletion = asyncio.create_task(
            client.delete(f"{users_url}{user_id}", headers=superuser_token_headers)
        )
        try:
            async with asyncio.timeout(5):
                while not deletion.done():
                    async with create_session() as observer:
                        if await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND wait_event_type = 'Lock' "
                                """AND query LIKE 'DELETE FROM "user" WHERE%')"""
                            )
                        ):
                            break
                    await asyncio.sleep(0.01)
                assert not deletion.done(), "deletion committed before SMTP started"
                deletion_waiting.set()
                await delivery_progress.wait()
                assert not deletion.done()
                send.assert_not_called()
                async with create_session() as observer:
                    await observer.exec(
                        text(
                            "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM "
                            "lease_expires_at - clock_timestamp()), 0)) FROM email_outbox"
                        )
                    )
                async with create_session() as contender, contender.begin():
                    assert await contender.scalar(
                        text("SELECT lease_expires_at <= now() FROM email_outbox")
                    )
                    assert await claim_email(contender) is None
        finally:
            start_smtp.set()
            assert await delivery
            assert (await deletion).status_code == 200
        send.assert_called_once()

    async with create_session() as session:
        job = (await session.exec(select(EmailOutbox))).one()
        assert job.delivered_at and job.failed_at is None and job.last_error is None
        assert job.user_id is None and job.lease_id is None


async def test_lease_is_exclusive_and_stale_completion_is_ignored(
    email_owner_id: UUID,
) -> None:
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            user_id=email_owner_id,
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


async def test_worker_renews_lease_while_rendering(
    monkeypatch: pytest.MonkeyPatch, email_owner_id: UUID
) -> None:
    polled = asyncio.Event()
    renewed = asyncio.Event()
    rendering = Event()
    finish_rendering = Event()
    render = email_worker.render_email

    def report_progress() -> None:
        if rendering.is_set():
            renewed.set()
        else:
            polled.set()

    def slow_render(claimed: ClaimedEmail) -> EmailData:
        rendering.set()
        assert finish_rendering.wait(timeout=10)
        return render(claimed)

    async def track_renewal(session: AsyncSession, claimed: ClaimedEmail) -> bool:
        row = await session.get(EmailOutbox, claimed.id)
        assert row and row.lease_expires_at
        previous_expiry = row.lease_expires_at
        assert await renew_email_lease(session, claimed)
        await session.refresh(row)
        assert row.lease_expires_at and row.lease_expires_at > previous_expiry
        return True

    monkeypatch.setattr(email_worker, "render_email", slow_render)
    monkeypatch.setattr("app.email_worker.renew_email_lease", track_renewal)
    monkeypatch.setattr("app.email_worker.LEASE_RENEW_INTERVAL", 0.01)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            user_id=email_owner_id,
            kind="welcome",
            recipient="slow-delivery@example.com",
            deduplication_key=uuid4().hex,
        )

    with patch.object(email_worker, "send_email") as send:
        delivery = asyncio.create_task(process_next_email(on_progress=report_progress))
        try:
            await asyncio.wait_for(renewed.wait(), timeout=5)
            assert polled.is_set() and not delivery.done()
            send.assert_not_called()
        finally:
            finish_rendering.set()
            assert await delivery
        send.assert_called_once()

    async with create_session() as session:
        row = (await session.exec(select(EmailOutbox))).one()
        assert row.delivered_at and row.lease_id is None


async def test_worker_retries_transient_smtp_error(
    monkeypatch: pytest.MonkeyPatch, email_owner_id: UUID
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
            user_id=email_owner_id,
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
    monkeypatch: pytest.MonkeyPatch, kind: EmailKind, email_owner_id: UUID
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
            user_id=email_owner_id,
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
    email_owner_id: UUID,
) -> None:
    delivered: list[str] = []

    def smtp(*, email_to: str, **_data: str) -> None:
        delivered.append(email_to)

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            user_id=email_owner_id,
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
            user_id=email_owner_id,
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
    monkeypatch: pytest.MonkeyPatch, email_owner_id: UUID
) -> None:
    def smtp(**_data: str) -> None:
        raise SMTPRecipientsRefused({"unavailable@example.com": (550, b"unavailable")})

    monkeypatch.setattr("app.email_worker.send_email", smtp)
    async with create_session() as session, session.begin():
        await enqueue_email(
            session,
            user_id=email_owner_id,
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
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", "Example, Inc.")
    monkeypatch.setattr(settings, "SMTP_USER", "test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-only")
    monkeypatch.setattr(settings, "SMTP_TLS", tls)
    monkeypatch.setattr(settings, "SMTP_SSL", ssl)
    with (
        patch("app.utils.SMTP") as smtp,
        patch("app.utils.SMTP_SSL") as smtp_ssl,
    ):
        connection = (smtp_ssl if ssl else smtp).return_value
        connection.send_message.side_effect = partial(SMTP.send_message, connection)
        connection.sendmail.return_value = {}
        response = await client.post(
            f"{settings.API_V1_STR}/utils/test-email/",
            headers=superuser_token_headers,
            params={"email_to": "recipient@example.com"},
        )
        assert response.status_code == 201
        assert connection.starttls.call_count == int(tls and not ssl)
        connection.login.assert_called_once_with("test", "test-only")
        connection.sendmail.assert_called_once()
        sender, recipients, wire_message, *_ = connection.sendmail.call_args.args
        assert sender == "from@example.com"
        assert recipients == ["recipient@example.com"]
        message = BytesParser(policy=default).parsebytes(wire_message)
        assert message["From"].addresses == (
            Address(display_name="Example, Inc.", addr_spec="from@example.com"),
        )
        assert message["To"] == "recipient@example.com"


@pytest.mark.parametrize("disabled_setting", ["SMTP_HOST", "EMAILS_FROM_EMAIL"])
async def test_admin_mail_probe_when_email_is_disabled(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    disabled_setting: str,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    monkeypatch.setattr(settings, disabled_setting, None)

    with (
        patch("app.utils.SMTP") as smtp,
        patch("app.utils.SMTP_SSL") as smtp_ssl,
    ):
        response = await client.post(
            f"{settings.API_V1_STR}/utils/test-email/",
            headers=superuser_token_headers,
            params={"email_to": "recipient@example.com"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Email delivery is unavailable"}
    smtp.assert_not_called()
    smtp_ssl.assert_not_called()


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
