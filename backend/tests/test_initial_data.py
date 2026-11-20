import asyncio

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.config import settings
from app.core.db import create_session, init_db
from app.core.security import verify_password
from app.models import User, UserCreate
from tests.utils.utils import random_email


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("is_active", "is_superuser"),
    [(True, False), (False, True)],
    ids=["regular-account", "inactive-admin"],
)
async def test_initializer_rejects_an_incompatible_existing_account(
    monkeypatch: pytest.MonkeyPatch, is_active: bool, is_superuser: bool
) -> None:
    email = random_email()
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", email)
    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", "bootstrap-password")

    async with create_session() as session:
        existing = await crud.create_user(
            session=session,
            user_create=UserCreate(
                email=email,
                password="existing-password",
                is_active=is_active,
                is_superuser=is_superuser,
            ),
        )
        original_hash = existing.hashed_password

    async with create_session() as initializer:
        with pytest.raises(RuntimeError, match="FIRST_SUPERUSER.*active superuser"):
            await init_db(initializer)

    async with create_session() as observer:
        user = (await observer.exec(select(User).where(User.email == email))).one()
        assert (user.is_active, user.is_superuser) == (is_active, is_superuser)
        assert user.hashed_password == original_hash


@pytest.mark.anyio
async def test_concurrent_initializers_preserve_the_first_superuser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = random_email()
    original_password = "initial-password"
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", email)
    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", original_password)

    get_user_by_email = crud.get_user_by_email
    before_create = asyncio.Barrier(2)

    async def synchronize_initial_lookups(
        *, session: AsyncSession, email: str
    ) -> User | None:
        user = await get_user_by_email(session=session, email=email)
        if user is None:
            await asyncio.wait_for(before_create.wait(), timeout=5)
        return user

    with monkeypatch.context() as lookup_patch:
        lookup_patch.setattr(crud, "get_user_by_email", synchronize_initial_lookups)
        async with create_session() as first, create_session() as second:
            await asyncio.gather(init_db(first), init_db(second))

    async with create_session() as observer:
        query = select(User).where(User.email == email)
        user = (await observer.exec(query)).one()
        assert user.is_active and user.is_superuser
        original_hash = user.hashed_password
        assert verify_password(original_password, original_hash)[0]

    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", "different-password")
    async with create_session() as retry:
        await init_db(retry)
        user = (await retry.exec(query)).one()
        assert user.hashed_password == original_hash


@pytest.mark.anyio
async def test_initializer_rejects_a_concurrent_regular_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = random_email()
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", email)

    get_user_by_email = crud.get_user_by_email

    async def insert_regular_account_after_lookup(
        *, session: AsyncSession, email: str
    ) -> User | None:
        user = await get_user_by_email(session=session, email=email)
        if user is None:
            async with create_session() as concurrent:
                await crud.create_user(
                    session=concurrent,
                    user_create=UserCreate(email=email, password="existing-password"),
                )
        return user

    monkeypatch.setattr(crud, "get_user_by_email", insert_regular_account_after_lookup)
    async with create_session() as initializer:
        with pytest.raises(crud.DuplicateEmailError):
            await init_db(initializer)

    async with create_session() as observer:
        user = (await observer.exec(select(User).where(User.email == email))).one()
        assert not user.is_superuser
        assert verify_password("existing-password", user.hashed_password)[0]
