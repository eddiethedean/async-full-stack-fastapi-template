from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from logging import INFO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from anyio import sleep_forever
from httpx import ASGITransport, AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.access_logs import scrub_error_report
from app.core.config import settings
from app.core.db import create_session
from app.core.maintenance import maintain_database
from app.email_outbox import EmailOutbox, enqueue_email
from app.email_worker import healthy, worker
from app.main import app
from app.models import AccountActionToken, AuthSession
from tests.utils.user import create_random_user

pytestmark = pytest.mark.anyio


async def test_readiness_reports_database_outage(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    readiness = f"{settings.API_V1_STR}/utils/readiness/"
    assert (await client.get(readiness)).status_code == 200

    @asynccontextmanager
    async def unavailable() -> AsyncIterator[None]:
        await sleep_forever()
        yield

    with monkeypatch.context() as patch:
        patch.setattr(
            "app.api.routes.utils.engine", SimpleNamespace(connect=unavailable)
        )
        patch.setattr(settings, "DB_READINESS_TIMEOUT_SECONDS", 0.01)
        assert (await client.get(readiness)).status_code == 503
        response = await client.get(f"{settings.API_V1_STR}/utils/health-check/")
        assert response.status_code == 200

    assert (await client.get(readiness)).status_code == 200


async def test_request_body_limit(client: AsyncClient) -> None:
    large = "x" * settings.MAX_REQUEST_BODY_BYTES
    response = await client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": "api@example.com", "password": large},
    )
    assert response.status_code == 413
    response = await client.post(
        f"{settings.API_V1_STR}/password-recovery",
        json={"email": large},
    )
    assert response.status_code == 413


async def test_request_logs_include_id_but_exclude_email(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email = "private-log-check@example.com"
    with caplog.at_level(INFO, logger="uvicorn.error"):
        response = await client.post(
            f"{settings.API_V1_STR}/password-recovery/{email}",
        )

    request_id = response.headers["X-Request-ID"]
    assert request_id in caplog.text
    assert email not in caplog.text
    assert "password-recovery/{email}" in caplog.text


async def test_unhandled_error_has_request_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unexpected_failure() -> None:
        raise RuntimeError("Synthetic connection failure")

    monkeypatch.setattr(
        "app.api.routes.utils.engine", SimpleNamespace(connect=unexpected_failure)
    )
    origin = settings.auth_origins[0]
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        with caplog.at_level(INFO, logger="uvicorn.error"):
            response = await client.get(
                f"{settings.API_V1_STR}/utils/readiness/", headers={"Origin": origin}
            )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] in caplog.text
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "x-request-id" in response.headers["Access-Control-Expose-Headers"].lower()


async def test_maintenance_clears_expired_tokens_and_mail_payloads(
    db: AsyncSession,
) -> None:
    user = await create_random_user(db=db)
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=60)
    session_id = uuid4()
    action_id = uuid4()
    with_token = uuid4().hex
    async with create_session() as session, session.begin():
        session.add(
            AuthSession(
                id=session_id,
                user_id=user.id,
                kind="bearer",
                auth_version=user.auth_version,
                expires_at=expired,
            )
        )
        session.add(
            AccountActionToken(
                id=action_id,
                user_id=user.id,
                purpose="password_reset",
                digest=uuid4().hex,
                auth_version=user.auth_version,
                created_at=expired,
                expires_at=expired,
            )
        )
        await enqueue_email(
            session,
            user_id=user.id,
            kind="password_reset",
            recipient=user.email,
            deduplication_key=with_token,
            token="temporary-credential",
            expires_delta=timedelta(seconds=-1),
        )
    await maintain_database()
    async with create_session() as session:
        assert await session.get(AuthSession, session_id) is None
        assert await session.get(AccountActionToken, action_id) is None
        row = (
            await session.exec(
                select(EmailOutbox).where(
                    col(EmailOutbox.deduplication_key) == with_token,
                )
            )
        ).one()
        assert row.failed_at and row.payload is None


async def test_email_worker_heartbeat_and_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heartbeat = tmp_path / "worker-health"
    monkeypatch.setattr("app.email_worker.HEARTBEAT", heartbeat)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    actions: list[Callable[[int, object], None]] = []

    def remember(_sig: int, handler: Callable[[int, object], None]) -> None:
        actions.append(handler)

    async def process(*, on_progress: Callable[[], None]) -> bool:
        on_progress()
        assert healthy()
        actions[-1](0, None)
        return False

    assert not healthy()
    monkeypatch.setattr("app.email_worker.signal.signal", remember)
    monkeypatch.setattr("app.email_worker.process_next_email", process)
    await worker()
    assert len(actions) == 2
    assert not heartbeat.exists()


def test_error_reporting_excludes_sensitive_request_metadata() -> None:
    event = scrub_error_report(
        {
            "request": {"url": "https://example.com/?token=secret"},
            "exception": {"values": [{"type": "ValueError", "value": "sensitive"}]},
            "transaction_info": {"source": "url"},
            "transaction": "/password-recovery/user@example.com",
            "spans": [{"description": "request detail", "data": {"query": "private"}}],
        },
        {},
    )
    assert "request" not in event
    assert event["transaction"] == "unmatched"
    assert event["exception"]["values"][0]["value"] == "ValueError"
    assert event["spans"] == [{}]
