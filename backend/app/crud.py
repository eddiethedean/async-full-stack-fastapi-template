import uuid

from psycopg.errors import UniqueViolation
from pydantic import MISSING
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import get_password_hash, verify_password
from app.models import Item, ItemCreate, User, UserCreate, UserUpdate


class DuplicateEmailError(Exception):
    """The user's email already exists."""


async def commit_user(session: AsyncSession, user: User) -> User:
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if (
            isinstance(error.orig, UniqueViolation)
            and error.orig.diag.constraint_name == "ix_user_email"
        ):
            raise DuplicateEmailError from error
        raise
    await session.refresh(user)
    return user


async def create_user(*, session: AsyncSession, user_create: UserCreate) -> User:
    hashed_password = await run_in_threadpool(get_password_hash, user_create.password)
    db_obj = User.model_validate(
        user_create, update={"hashed_password": hashed_password}
    )
    return await commit_user(session, db_obj)


async def update_user(
    *, session: AsyncSession, db_user: User, user_in: UserUpdate
) -> User:
    user_data = user_in.model_dump(exclude_unset=True, exclude={"password"})
    if user_in.password is not MISSING:
        user_data["hashed_password"] = await run_in_threadpool(
            get_password_hash, user_in.password
        )
    db_user.sqlmodel_update(user_data)
    return await commit_user(session, db_user)


async def get_user_by_email(*, session: AsyncSession, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    result = await session.exec(statement)
    return result.first()


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


async def authenticate(
    *, session: AsyncSession, email: str, password: str
) -> User | None:
    db_user = await get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        await run_in_threadpool(verify_password, password, DUMMY_HASH)
        return None
    verified, updated_password_hash = await run_in_threadpool(
        verify_password, password, db_user.hashed_password
    )
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def create_item(
    *, session: AsyncSession, item_in: ItemCreate, owner_id: uuid.UUID
) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    await session.commit()
    await session.refresh(db_item)
    return db_item
