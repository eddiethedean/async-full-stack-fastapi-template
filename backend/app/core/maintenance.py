import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import delete, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import SQLModel, col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import create_session
from app.core.rate_limits import purge_rate_limit_buckets
from app.email_outbox import EmailOutbox, database_now
from app.models import AccountActionToken, AuthSession

logger = logging.getLogger(__name__)
BATCH_SIZE = 100
MAINTENANCE_INTERVAL_SECONDS = 300


async def delete_expired_rows(
    session: AsyncSession,
    model: type[SQLModel],
    row_id: Mapped[uuid.UUID],
    condition: ColumnElement[bool],
) -> None:
    batch = (
        select(row_id)
        .where(condition)
        .order_by(row_id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    await session.exec(delete(model).where(row_id.in_(batch)))


async def maintain_database() -> None:
    async with create_session() as session, session.begin():
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(243380410)")
        )
        if not acquired:
            return
        now = await database_now(session)
        retention_cutoff = now - timedelta(days=7)
        await delete_expired_rows(
            session,
            AuthSession,
            col(AuthSession.id),
            or_(
                col(AuthSession.expires_at) <= now,
                col(AuthSession.revoked_at) <= retention_cutoff,
            ),
        )
        await delete_expired_rows(
            session,
            AccountActionToken,
            col(AccountActionToken.id),
            col(AccountActionToken.expires_at) <= now,
        )
        await purge_rate_limit_buckets(session)
        expired = (
            select(EmailOutbox.id)
            .where(
                col(EmailOutbox.payload).is_not(None),
                col(EmailOutbox.expires_at) <= now,
                or_(
                    col(EmailOutbox.lease_expires_at).is_(None),
                    col(EmailOutbox.lease_expires_at) <= now,
                ),
            )
            .order_by(col(EmailOutbox.id))
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        await session.exec(
            update(EmailOutbox)
            .where(col(EmailOutbox.id).in_(expired))
            .values(
                failed_at=now,
                last_error="expired",
                payload=None,
                key_version=None,
                lease_id=None,
                lease_expires_at=None,
            )
        )
        await delete_expired_rows(
            session,
            EmailOutbox,
            col(EmailOutbox.id),
            or_(
                col(EmailOutbox.delivered_at) <= retention_cutoff,
                col(EmailOutbox.failed_at) <= retention_cutoff,
            ),
        )


async def maintenance_loop() -> None:
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            await maintain_database()
        except SQLAlchemyError:
            logger.warning("database_maintenance datastore_unavailable")
