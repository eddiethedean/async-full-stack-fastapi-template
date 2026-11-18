import hashlib
import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import AccountActionToken, AuthSession, User, get_datetime_utc

AccountAction = Literal["password_reset", "invite"]


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def lock_user(session: AsyncSession, user_id: UUID) -> User | None:
    statement = (
        select(User)
        .where(col(User.id) == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.exec(statement)).one_or_none()


async def advance_auth_version(session: AsyncSession, user: User) -> None:
    """Invalidate credentials for a locked user in the caller's transaction."""
    user.auth_version += 1
    session.add(user)
    await revoke_user_sessions(session, user)


async def revoke_user_sessions(session: AsyncSession, user: User) -> None:
    # Let the caller handle errors from flushing changes to the user row.
    with session.no_autoflush:
        await session.exec(
            update(AuthSession)
            .where(
                col(AuthSession.user_id) == user.id,
                col(AuthSession.revoked_at).is_(None),
            )
            .values(revoked_at=get_datetime_utc())
        )


def issue_account_action(
    session: AsyncSession,
    user: User,
    *,
    purpose: AccountAction,
    expires_delta: timedelta,
) -> str:
    """Issue a link for a locked or newly created user without committing."""
    token = generate_opaque_token()
    now = get_datetime_utc()
    session.add(
        AccountActionToken(
            user_id=user.id,
            purpose=purpose,
            digest=token_digest(token),
            auth_version=user.auth_version,
            created_at=now,
            expires_at=now + expires_delta,
        )
    )
    return token


def _available_action(
    token: str, purpose: AccountAction
) -> tuple[ColumnElement[bool], ...]:
    return (
        col(AccountActionToken.digest) == token_digest(token),
        col(AccountActionToken.purpose) == purpose,
        col(AccountActionToken.consumed_at).is_(None),
        col(AccountActionToken.expires_at) > get_datetime_utc(),
    )


async def find_account_action_user_id(
    session: AsyncSession, token: str, *, purpose: AccountAction
) -> UUID | None:
    statement = select(AccountActionToken.user_id).where(
        *_available_action(token, purpose)
    )
    return (await session.exec(statement)).one_or_none()


async def consume_account_action(
    session: AsyncSession,
    token: str,
    *,
    purpose: AccountAction,
    require_active: bool = True,
) -> User | None:
    """Lock the user before consuming the link, in the caller's transaction."""
    user_id = await find_account_action_user_id(session, token, purpose=purpose)
    if user_id is None:
        return None
    user = await lock_user(session, user_id)
    if user is None or (require_active and not user.is_active):
        return None
    result = await session.exec(
        update(AccountActionToken)
        .where(
            *_available_action(token, purpose),
            col(AccountActionToken.user_id) == user_id,
            col(AccountActionToken.auth_version) == user.auth_version,
        )
        .values(consumed_at=get_datetime_utc())
        .returning(col(AccountActionToken.id))
    )
    return user if result.one_or_none() is not None else None
