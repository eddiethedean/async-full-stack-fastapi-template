from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.security import DUMMY_HASH, verify_password
from app.models import Item, User, UserCreate, UserUpdate
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


async def test_create_user(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    assert user.email == email
    assert hasattr(user, "hashed_password")


async def test_expected_user_conflict_rolls_back_session(db: AsyncSession) -> None:
    user_in = UserCreate(email=random_email(), password=random_lower_string())
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id

    with pytest.raises(crud.DuplicateEmailError):
        await crud.create_user(session=db, user_create=user_in)

    assert await db.get(User, user_id) is not None


async def test_other_integrity_errors_are_not_email_conflicts(db: AsyncSession) -> None:
    user_in = UserCreate(email=random_email(), password=random_lower_string())
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id
    db.add(Item(title="Invalid owner", owner_id=uuid4()))

    with pytest.raises(IntegrityError):
        await crud.commit_user(db, user)

    assert await db.get(User, user_id) is not None


async def test_authenticate_user(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    authenticated_user = await crud.authenticate(
        session=db, email=email, password=password
    )
    assert authenticated_user
    assert user.email == authenticated_user.email


async def test_not_authenticate_user(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user = await crud.authenticate(session=db, email=email, password=password)
    assert user is None


async def test_check_if_user_is_active(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    assert user.is_active is True


async def test_check_if_user_is_active_inactive(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password, is_active=False)
    user = await crud.create_user(session=db, user_create=user_in)
    assert user.is_active is False


async def test_check_if_user_is_superuser(db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password, is_superuser=True)
    user = await crud.create_user(session=db, user_create=user_in)
    assert user.is_superuser is True


async def test_check_if_user_is_superuser_normal_user(db: AsyncSession) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    assert user.is_superuser is False


async def test_get_user(db: AsyncSession) -> None:
    password = random_lower_string()
    username = random_email()
    user_in = UserCreate(email=username, password=password, is_superuser=True)
    user = await crud.create_user(session=db, user_create=user_in)
    user_2 = await db.get(User, user.id)
    assert user_2
    assert user.email == user_2.email
    assert jsonable_encoder(user) == jsonable_encoder(user_2)


async def test_update_user(db: AsyncSession) -> None:
    password = random_lower_string()
    email = random_email()
    user_in = UserCreate(email=email, password=password, is_superuser=True)
    user = await crud.create_user(session=db, user_create=user_in)
    new_password = random_lower_string()
    user_in_update = UserUpdate(password=new_password, is_superuser=True)
    if user.id is not None:
        await crud.update_user(session=db, db_user=user, user_in=user_in_update)
    user_2 = await db.get(User, user.id)
    assert user_2
    assert user.email == user_2.email
    verified, _ = verify_password(new_password, user_2.hashed_password)
    assert verified


async def test_authenticate_user_rehashes_outdated_argon2_parameters(
    db: AsyncSession,
) -> None:
    email = random_email()
    password = random_lower_string()
    old_hash = Argon2Hasher(memory_cost=8192).hash(password)
    user = User(email=email, hashed_password=old_hash)
    db.add(user)
    await db.commit()

    authenticated_user = await crud.authenticate(
        session=db, email=email, password=password
    )
    assert authenticated_user
    assert authenticated_user.email == email

    await db.refresh(authenticated_user)

    assert authenticated_user.hashed_password != old_hash

    verified, updated_hash = verify_password(
        password, authenticated_user.hashed_password
    )
    assert verified
    assert updated_hash is None


async def test_authenticate_unknown_user_runs_dummy_argon2_verification(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = random_lower_string()
    calls: list[tuple[str, str]] = []

    def verify(plain: str, hashed: str) -> tuple[bool, str | None]:
        calls.append((plain, hashed))
        return verify_password(plain, hashed)

    monkeypatch.setattr(crud, "verify_password", verify)
    assert (
        await crud.authenticate(session=db, email=random_email(), password=password)
        is None
    )
    assert DUMMY_HASH.startswith("$argon2id$")
    assert calls == [(password, DUMMY_HASH)]
