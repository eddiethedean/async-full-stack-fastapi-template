import argparse
import asyncio
import logging
import signal
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from smtplib import SMTPRecipientsRefused, SMTPResponseException

from cryptography.fernet import InvalidToken
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import create_session, engine
from app.email_outbox import (
    LEASE_DURATION,
    ClaimedEmail,
    ClaimOutcome,
    active_key,
    claim_email,
    complete_email,
    database_now,
    get_cipher,
    lock_current_email_lease,
    queue_stats,
    renew_email_lease,
)
from app.utils import (
    EmailData,
    generate_invitation_email,
    generate_new_account_email,
    generate_reset_password_email,
    send_email,
)

logger = logging.getLogger(__name__)
LEASE_RENEW_INTERVAL = LEASE_DURATION.total_seconds() / 4
HEARTBEAT = Path("/tmp/asf-email-worker-heartbeat")
HEARTBEAT_MAX_AGE_SECONDS = 150


def healthy() -> bool:
    if not settings.emails_enabled:
        return True
    try:
        return time.time() - HEARTBEAT.stat().st_mtime < HEARTBEAT_MAX_AGE_SECONDS
    except FileNotFoundError:
        return False


def record_heartbeat() -> None:
    try:
        HEARTBEAT.touch()
    except OSError:
        logger.warning("email_outbox heartbeat_unavailable")


def render_email(claimed: ClaimedEmail) -> EmailData:
    token = None
    if claimed.payload is not None and claimed.key_version is not None:
        token = get_cipher(claimed.key_version).decrypt(claimed.payload).decode()

    if claimed.kind == "password_reset" and token is not None:
        return generate_reset_password_email(
            claimed.recipient, claimed.recipient, token
        )
    if claimed.kind == "invite" and token is not None:
        return generate_invitation_email(claimed.recipient, token)
    if claimed.kind == "welcome":
        return generate_new_account_email(claimed.recipient)
    raise ValueError("Missing email payload")


async def keep_email_claim(
    claimed: ClaimedEmail,
    finished: asyncio.Event,
    on_progress: Callable[[], None] | None,
    delivery_session: AsyncSession | None,
) -> None:
    while not finished.is_set():
        try:
            await asyncio.wait_for(finished.wait(), timeout=LEASE_RENEW_INTERVAL)
            return
        except TimeoutError:
            pass
        if delivery_session is not None:
            # The row lock protects the claim during SMTP. Updating the row
            # repeatedly here can recheck its FK against a user being deleted.
            await database_now(delivery_session)
        else:
            try:
                async with create_session() as session, session.begin():
                    renewed = await renew_email_lease(session, claimed)
            except SQLAlchemyError:
                logger.warning(
                    "email_outbox id=%s lease_renewal_unavailable", claimed.id
                )
                continue
            if not renewed:
                return
        if on_progress is not None:
            on_progress()


@asynccontextmanager
async def monitor_email_claim(
    claimed: ClaimedEmail,
    on_progress: Callable[[], None] | None,
    delivery_session: AsyncSession | None = None,
) -> AsyncIterator[None]:
    finished = asyncio.Event()
    renewal = asyncio.create_task(
        keep_email_claim(claimed, finished, on_progress, delivery_session)
    )
    try:
        yield
    finally:
        finished.set()
        await renewal


async def deliver_with_lease(
    session: AsyncSession,
    claimed: ClaimedEmail,
    on_progress: Callable[[], None] | None,
) -> bool:
    async with monitor_email_claim(claimed, on_progress):
        email = await run_in_threadpool(render_email, claimed)
    # The delete trigger updates this row. Hold its lock until the SMTP
    # attempt and its recorded outcome are complete.
    if not await lock_current_email_lease(session, claimed):
        return False
    async with monitor_email_claim(claimed, on_progress, session):
        await run_in_threadpool(
            send_email,
            email_to=claimed.recipient,
            subject=email.subject,
            html_content=email.html_content,
            message_id=f"{claimed.id}@email-outbox",
        )
    return True


async def process_next_email(*, on_progress: Callable[[], None] | None = None) -> bool:
    """Return whether the worker claimed or retired a job in this poll."""
    async with create_session() as session, session.begin():
        claimed = await claim_email(session)
    if on_progress is not None:
        on_progress()
    if claimed is None:
        return False
    if claimed is ClaimOutcome.RETIRED:
        return True

    error = None
    retryable = False
    can_complete = True
    async with create_session() as session, session.begin():
        try:
            if datetime.now(UTC) >= claimed.expires_at:
                error = "expired"
            else:
                can_complete = await deliver_with_lease(session, claimed, on_progress)
        except SMTPRecipientsRefused as exc:
            error = "smtp_recipient"
            retryable = any(400 <= code < 500 for code, _ in exc.recipients.values())
        except SMTPResponseException as exc:
            error = "smtp_rejected"
            retryable = 400 <= exc.smtp_code < 500
        except OSError:
            error = "smtp_unavailable"
            retryable = True
        except InvalidToken, ValueError:
            error = "email_payload"
            retryable = True

        completed = can_complete and await complete_email(
            session, claimed, error=error, retryable=retryable
        )
    logger.info(
        "email_outbox id=%s result=%s",
        claimed.id,
        ("lost_lease" if not completed else error or "delivered"),
    )
    return True


async def worker() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    HEARTBEAT.unlink(missing_ok=True)

    def signal_stop(_signal: int, _frame: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal_stop)
    next_report = 0.0
    try:
        if not settings.emails_enabled:
            logger.info("email_outbox disabled")
            await stop.wait()
            return
        get_cipher(active_key())
        while not stop.is_set():
            try:
                had_work = await process_next_email(on_progress=record_heartbeat)
                if loop.time() >= next_report:
                    async with create_session() as session:
                        depth, oldest = await queue_stats(session)
                    age = (
                        max(0.0, (datetime.now(UTC) - oldest).total_seconds())
                        if oldest
                        else 0.0
                    )
                    logger.info(
                        "email_outbox pending=%d oldest_seconds=%.0f", depth, age
                    )
                    next_report = loop.time() + 60
                if had_work:
                    continue
            except SQLAlchemyError:
                logger.warning("email_outbox datastore_unavailable")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        HEARTBEAT.unlink(missing_ok=True)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process queued account emails")
    parser.add_argument("--healthcheck", action="store_true")
    if parser.parse_args().healthcheck:
        raise SystemExit(0 if healthy() else 1)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(worker(), loop_factory=asyncio.SelectorEventLoop)
