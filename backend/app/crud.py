import uuid
from typing import Literal

from fastapi import HTTPException
from psycopg.errors import SerializationFailure, UniqueViolation
from pydantic import MISSING
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import (
    get_password_hash,
    normalize_account_email,
    verify_password,
)
from app.core.sessions import advance_auth_version, lock_user
from app.models import Item, ItemCreate, User, UserCreate, UserUpdate, UserUpdateMe


class DuplicateEmailError(Exception):
    """The user's email already exists."""


async def flush_user(session: AsyncSession, user: User) -> None:
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        check_email_conflict(error)
        raise


def check_email_conflict(error: IntegrityError) -> None:
    if (
        isinstance(error.orig, UniqueViolation)
        and error.orig.diag.constraint_name == "ix_user_email"
    ):
        raise DuplicateEmailError from error


async def commit_user(session: AsyncSession, user: User) -> User:
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        check_email_conflict(error)
        raise
    await session.refresh(user)
    return user


async def create_user(*, session: AsyncSession, user_create: UserCreate) -> User:
    hashed_password = await run_in_threadpool(get_password_hash, user_create.password)
    db_obj = User.model_validate(
        user_create, update={"hashed_password": hashed_password}
    )
    return await commit_user(session, db_obj)


async def lock_active_superuser_removal(session: AsyncSession) -> None:
    # Serialize removals before locking individual user rows.
    await session.exec(select(func.pg_advisory_xact_lock(243380411)))


async def require_other_active_superuser(session: AsyncSession, user: User) -> None:
    if not (user.is_active and user.is_superuser):
        return
    statement = (
        select(User.id)
        .where(
            col(User.is_active).is_(True),
            col(User.is_superuser).is_(True),
            col(User.id) != user.id,
        )
        .limit(1)
        .with_for_update(read=True)
    )
    # A row lock detects superuser changes hidden by a repeatable-read snapshot.
    try:
        other = (await session.exec(statement)).first()
    except OperationalError as error:
        if not isinstance(error.orig, SerializationFailure):
            raise
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Superuser membership changed. Retry the request."
        ) from error

    if other is None:
        raise HTTPException(
            status_code=409, detail="Cannot remove the last active superuser"
        )


async def update_user(
    *,
    session: AsyncSession,
    db_user: User,
    user_in: UserUpdate | UserUpdateMe,
    expected_auth_version: int | None = None,
) -> User | None:
    user_data = user_in.model_dump(exclude_unset=True, exclude={"password"})
    new_hash = None
    if isinstance(user_in, UserUpdate) and user_in.password is not MISSING:
        new_hash = await run_in_threadpool(get_password_hash, user_in.password)
    may_remove_superuser = (
        user_data.get("is_superuser") is False or user_data.get("is_active") is False
    )
    if may_remove_superuser:
        await lock_active_superuser_removal(session)
    user = await lock_user(session, db_user.id)
    if user is None:
        return None
    # A credential change may have committed after the request authenticated.
    if expected_auth_version is not None and (
        not user.is_active or user.auth_version != expected_auth_version
    ):
        return None
    email_changed = user_data.get("email", user.email) != user.email
    if user.invitation_pending and user_data.get("is_active"):
        raise HTTPException(
            status_code=409,
            detail="Accept the invitation before activating this account",
        )
    if user.invitation_pending and (email_changed or new_hash is not None):
        raise HTTPException(
            status_code=409,
            detail="Accept the invitation before changing email or password",
        )
    if may_remove_superuser:
        await require_other_active_superuser(session, user)
    credentials_changed = (
        new_hash is not None
        or email_changed
        or (user.is_active and user_data.get("is_active") is False)
    )
    user.sqlmodel_update(user_data)
    if new_hash is not None:
        user.hashed_password = new_hash
    if credentials_changed:
        await advance_auth_version(session, user)
    return await commit_user(session, user)


async def change_password(
    *, session: AsyncSession, user: User, current_password: str, new_password: str
) -> Literal["changed", "incorrect", "unchanged"]:
    previous_hash = user.hashed_password
    previous_version = user.auth_version
    verified, _ = await run_in_threadpool(
        verify_password, current_password, previous_hash
    )
    if not verified:
        return "incorrect"
    if current_password == new_password:
        return "unchanged"
    new_hash = await run_in_threadpool(get_password_hash, new_password)
    locked = await lock_user(session, user.id)
    if (
        locked is None
        or not locked.is_active
        or locked.auth_version != previous_version
    ):
        return "incorrect"
    await set_password(session, locked, new_hash)
    await session.commit()
    return "changed"


async def set_password(session: AsyncSession, user: User, new_hash: str) -> None:
    """Change the password of a locked user in the caller's transaction."""
    user.hashed_password = new_hash
    await advance_auth_version(session, user)


async def get_user_by_email(*, session: AsyncSession, email: str) -> User | None:
    statement = select(User).where(User.email == normalize_account_email(email))
    result = await session.exec(statement)
    return result.first()


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


async def authenticate(
    *, session: AsyncSession, email: str, password: str, commit: bool = True
) -> User | None:
    db_user = await get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        await run_in_threadpool(verify_password, password, DUMMY_HASH)
        return None
    previous_version = db_user.auth_version
    previous_hash = db_user.hashed_password
    verified, updated_password_hash = await run_in_threadpool(
        verify_password, password, previous_hash
    )
    if not verified:
        return None
    user = await lock_user(session, db_user.id)
    if user is None or not user.is_active or user.auth_version != previous_version:
        return None
    if updated_password_hash and user.hashed_password == previous_hash:
        user.hashed_password = updated_password_hash
        session.add(user)
    # Login routes hold this lock until the session record is committed.
    if commit:
        await session.commit()
    return user


async def create_item(
    *, session: AsyncSession, item_in: ItemCreate, owner_id: uuid.UUID
) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    await session.commit()
    await session.refresh(db_item)
    return db_item
