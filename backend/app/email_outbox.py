import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import CheckConstraint, Index, and_, or_, text, update
from sqlmodel import Field, SQLModel, col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

EmailKind = Literal["password_reset", "invite", "welcome"]
MAX_ATTEMPTS = 6
LEASE_DURATION = timedelta(minutes=2)


class EmailOutbox(SQLModel, table=True):
    __tablename__ = "email_outbox"
    __table_args__ = (
        Index(
            "ix_email_outbox_pending",
            "available_at",
            postgresql_where=text("delivered_at IS NULL AND failed_at IS NULL"),
        ),
        CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: str = Field(max_length=24)
    recipient: str = Field(max_length=255)
    deduplication_key: str = Field(max_length=128, unique=True)
    payload: bytes | None = None
    key_version: str | None = Field(default=None, max_length=32)
    attempts: int = 0
    created_at: datetime
    available_at: datetime
    expires_at: datetime
    lease_id: uuid.UUID | None = None
    lease_expires_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=32)


@dataclass(frozen=True)
class ClaimedEmail:
    id: uuid.UUID
    lease_id: uuid.UUID
    kind: str
    recipient: str
    payload: bytes | None
    key_version: str | None
    expires_at: datetime


def active_key() -> str:
    if not settings.EMAIL_OUTBOX_KEYS and settings.FASTAPI_ENV == "development":
        return "development"
    return settings.EMAIL_OUTBOX_KEY_VERSION


def get_cipher(key_version: str) -> Fernet:
    key: SecretStr | None = settings.EMAIL_OUTBOX_KEYS.get(key_version)
    if key is not None:
        return Fernet(key.get_secret_value())
    if key_version == "development" and settings.FASTAPI_ENV == "development":
        digest = hashlib.sha256(
            b"development email outbox\x00" + settings.SECRET_KEY.encode()
        ).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    raise ValueError("Email outbox key unavailable")


def enqueue_email(
    session: AsyncSession,
    *,
    kind: EmailKind,
    recipient: str,
    deduplication_key: str,
    token: str | None = None,
    expires_delta: timedelta = timedelta(hours=48),
) -> None:
    now = datetime.now(UTC)
    key_version = active_key() if token is not None else None
    session.add(
        EmailOutbox(
            kind=kind,
            recipient=recipient,
            deduplication_key=deduplication_key,
            payload=(
                get_cipher(key_version).encrypt(token.encode())
                if token is not None and key_version is not None
                else None
            ),
            key_version=key_version,
            created_at=now,
            available_at=now,
            expires_at=now + expires_delta,
        )
    )


async def database_now(session: AsyncSession) -> datetime:
    return (await session.exec(select(func.now()))).one()


async def claim_email(session: AsyncSession) -> ClaimedEmail | None:
    now = await database_now(session)
    row = (
        await session.exec(
            select(EmailOutbox)
            .where(
                col(EmailOutbox.delivered_at).is_(None),
                col(EmailOutbox.failed_at).is_(None),
                col(EmailOutbox.available_at) <= now,
                or_(
                    col(EmailOutbox.lease_expires_at).is_(None),
                    col(EmailOutbox.lease_expires_at) <= now,
                ),
            )
            .order_by(col(EmailOutbox.available_at), col(EmailOutbox.id))
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).first()
    if row is None:
        return None
    if row.expires_at <= now or row.attempts >= MAX_ATTEMPTS:
        row.last_error = "expired" if row.expires_at <= now else "lease_exhausted"
        clear_payload(row)
        row.failed_at = now
        return None

    lease_id = uuid.uuid4()
    row.lease_id = lease_id
    row.lease_expires_at = now + LEASE_DURATION
    row.attempts += 1
    return ClaimedEmail(
        row.id,
        lease_id,
        row.kind,
        row.recipient,
        row.payload,
        row.key_version,
        row.expires_at,
    )


def clear_lease(row: EmailOutbox) -> None:
    row.lease_id = None
    row.lease_expires_at = None


def clear_payload(row: EmailOutbox) -> None:
    clear_lease(row)
    row.payload = None
    row.key_version = None


async def renew_email_lease(session: AsyncSession, claimed: ClaimedEmail) -> bool:
    now = func.clock_timestamp()
    result = await session.exec(
        update(EmailOutbox)
        .where(
            col(EmailOutbox.id) == claimed.id,
            col(EmailOutbox.lease_id) == claimed.lease_id,
            col(EmailOutbox.lease_expires_at) > now,
        )
        .values(lease_expires_at=now + LEASE_DURATION)
        .returning(col(EmailOutbox.id))
    )
    return result.one_or_none() is not None


async def complete_email(
    session: AsyncSession,
    claimed: ClaimedEmail,
    *,
    error: str | None = None,
    retryable: bool = False,
) -> bool:
    now = await database_now(session)
    row = (
        await session.exec(
            select(EmailOutbox)
            .where(
                col(EmailOutbox.id) == claimed.id,
                col(EmailOutbox.lease_id) == claimed.lease_id,
                col(EmailOutbox.lease_expires_at) > now,
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        return False

    row.last_error = error
    if error is None:
        row.delivered_at = now
        clear_payload(row)
    else:
        next_attempt = now + timedelta(seconds=min(30 * 2 ** (row.attempts - 1), 900))
        if retryable and row.attempts < MAX_ATTEMPTS and next_attempt < row.expires_at:
            row.available_at = next_attempt
            clear_lease(row)
        else:
            row.failed_at = now
            clear_payload(row)
    return True


async def queue_stats(session: AsyncSession) -> tuple[int, datetime | None]:
    count, oldest = (
        await session.exec(
            select(func.count(), func.min(col(EmailOutbox.created_at))).where(
                and_(
                    col(EmailOutbox.delivered_at).is_(None),
                    col(EmailOutbox.failed_at).is_(None),
                )
            )
        )
    ).one()
    return int(count), oldest
