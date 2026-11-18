import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from smtplib import SMTPRecipientsRefused, SMTPResponseException

from cryptography.fernet import InvalidToken
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import create_session, engine
from app.email_outbox import (
    ClaimedEmail,
    active_key,
    claim_email,
    complete_email,
    get_cipher,
    queue_stats,
)
from app.utils import (
    generate_invitation_email,
    generate_new_account_email,
    generate_reset_password_email,
    send_email,
)

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/asf-email-worker-heartbeat")
HEARTBEAT_MAX_AGE_SECONDS = 150


def healthy() -> bool:
    if not settings.emails_enabled:
        return True
    try:
        return time.time() - HEARTBEAT.stat().st_mtime < HEARTBEAT_MAX_AGE_SECONDS
    except FileNotFoundError:
        return False


def deliver_email(claimed: ClaimedEmail) -> None:
    token = None
    if claimed.payload is not None and claimed.key_version is not None:
        token = get_cipher(claimed.key_version).decrypt(claimed.payload).decode()

    if claimed.kind == "password_reset" and token is not None:
        email = generate_reset_password_email(
            claimed.recipient, claimed.recipient, token
        )
    elif claimed.kind == "invite" and token is not None:
        email = generate_invitation_email(claimed.recipient, token)
    elif claimed.kind == "welcome":
        email = generate_new_account_email(claimed.recipient)
    else:
        raise ValueError("Missing email payload")

    send_email(
        email_to=claimed.recipient,
        subject=email.subject,
        html_content=email.html_content,
        message_id=f"{claimed.id}@email-outbox",
    )


async def process_next_email() -> bool:
    async with create_session() as session, session.begin():
        claimed = await claim_email(session)
    if claimed is None:
        return False

    error = None
    retryable = False
    try:
        if datetime.now(UTC) >= claimed.expires_at:
            error = "expired"
        else:
            await run_in_threadpool(deliver_email, claimed)
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

    async with create_session() as session, session.begin():
        completed = await complete_email(
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
                had_work = await process_next_email()
                HEARTBEAT.touch()
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
