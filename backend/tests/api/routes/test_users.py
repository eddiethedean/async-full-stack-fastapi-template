import asyncio
import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.config import settings
from app.core.db import create_session, engine
from app.core.security import verify_password
from app.core.sessions import advance_auth_version, lock_user
from app.email_outbox import EmailOutbox
from app.models import User, UserCreate
from tests.utils.user import create_random_user, user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


async def test_users_pagination_bounds(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        params={"limit": 101},
    )
    assert response.status_code == 422


async def test_get_users_superuser_me(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    r = await client.get(
        f"{settings.API_V1_STR}/users/me", headers=superuser_token_headers
    )
    current_user = r.json()
    assert current_user
    assert current_user["is_active"] is True
    assert current_user["is_superuser"]
    assert current_user["email"] == settings.FIRST_SUPERUSER


async def test_get_users_normal_user_me(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = await client.get(
        f"{settings.API_V1_STR}/users/me", headers=normal_user_token_headers
    )
    current_user = r.json()
    assert current_user
    assert current_user["is_active"] is True
    assert current_user["is_superuser"] is False
    assert current_user["email"] == settings.EMAIL_TEST_USER


async def test_create_user_new_email(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    with (
        patch("app.core.config.settings.SMTP_HOST", "smtp.example.com"),
        patch("app.core.config.settings.SMTP_USER", "admin@example.com"),
    ):
        username = random_email()
        password = random_lower_string()
        data = {"email": username, "password": password}
        r = await client.post(
            f"{settings.API_V1_STR}/users/",
            headers=superuser_token_headers,
            json=data,
        )
        assert 200 <= r.status_code < 300
        created_user = r.json()
        user = await crud.get_user_by_email(session=db, email=username)
        assert user
        assert user.email == created_user["email"]


@pytest.mark.parametrize("is_active", [True, False], ids=["active", "inactive"])
async def test_welcome_email_for_admin_provisioned_user(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    is_active: bool,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "from@example.com")
    email = random_email()
    response = await client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json={
            "email": email,
            "password": random_lower_string(),
            "is_active": is_active,
        },
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is is_active

    jobs = (
        await db.exec(select(EmailOutbox).where(col(EmailOutbox.recipient) == email))
    ).all()
    if is_active:
        assert len(jobs) == 1
        assert jobs[0].kind == "welcome"
        assert str(jobs[0].user_id) == response.json()["id"]
    else:
        assert not jobs


async def test_get_existing_user_as_superuser(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id
    r = await client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert 200 <= r.status_code < 300
    api_user = r.json()
    existing_user = await crud.get_user_by_email(session=db, email=username)
    assert existing_user
    assert existing_user.email == api_user["email"]


async def test_get_non_existing_user_as_superuser(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    r = await client.get(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "User not found"}


async def test_get_existing_user_current_user(
    client: AsyncClient, db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id

    login_data = {
        "username": username,
        "password": password,
    }
    r = await client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    a_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {a_token}"}

    r = await client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=headers,
    )
    assert 200 <= r.status_code < 300
    api_user = r.json()
    existing_user = await crud.get_user_by_email(session=db, email=username)
    assert existing_user
    assert existing_user.email == api_user["email"]


async def test_get_existing_user_permissions_error(
    db: AsyncSession,
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    user = await create_random_user(db)

    r = await client.get(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "The user doesn't have enough privileges"}


async def test_get_non_existing_user_permissions_error(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    user_id = uuid.uuid4()

    r = await client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "The user doesn't have enough privileges"}


async def test_create_user_existing_username(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    # username = email
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    await crud.create_user(session=db, user_create=user_in)
    data = {"email": username, "password": password}
    r = await client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json=data,
    )
    created_user = r.json()
    assert r.status_code == 409
    assert created_user["detail"] == "User with this email already exists"


async def test_create_user_by_normal_user(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    username = random_email()
    password = random_lower_string()
    data = {"email": username, "password": password}
    r = await client.post(
        f"{settings.API_V1_STR}/users/",
        headers=normal_user_token_headers,
        json=data,
    )
    assert r.status_code == 403


async def test_retrieve_users(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    await crud.create_user(session=db, user_create=user_in)

    username2 = random_email()
    password2 = random_lower_string()
    user_in2 = UserCreate(email=username2, password=password2)
    await crud.create_user(session=db, user_create=user_in2)

    r = await client.get(
        f"{settings.API_V1_STR}/users/", headers=superuser_token_headers
    )
    all_users = r.json()

    assert len(all_users["data"]) > 1
    assert "count" in all_users
    for item in all_users["data"]:
        assert "email" in item


async def test_update_user_me(client: AsyncClient, db: AsyncSession) -> None:
    previous_email = random_email()
    password = random_lower_string()
    await crud.create_user(
        session=db, user_create=UserCreate(email=previous_email, password=password)
    )
    headers = await user_authentication_headers(
        client=client, email=previous_email, password=password
    )
    full_name = "Updated Name"
    email = random_email()
    data = {"full_name": full_name, "email": email}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/me",
        headers=headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()
    assert updated_user["email"] == email
    assert updated_user["full_name"] == full_name

    user_query = select(User).where(User.email == email)
    user_db = (await db.exec(user_query)).first()
    assert user_db
    assert user_db.email == email
    assert user_db.full_name == full_name
    assert (
        await client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    ).status_code == 401


async def test_profile_update_rechecks_credentials_after_lock(
    client: AsyncClient, db: AsyncSession
) -> None:
    email = random_email()
    password = random_lower_string()
    user = await crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    headers = await user_authentication_headers(
        client=client, email=email, password=password
    )

    async def rotate_credentials_before_profile_lock(
        profile_session: AsyncSession, user_id: uuid.UUID
    ) -> User | None:
        async with create_session() as change_session:
            changing_user = await lock_user(change_session, user_id)
            assert changing_user is not None
            await advance_auth_version(change_session, changing_user)
            await change_session.commit()
        return await lock_user(profile_session, user_id)

    with patch(
        "app.crud.lock_user", side_effect=rotate_credentials_before_profile_lock
    ):
        response = await client.patch(
            f"{settings.API_V1_STR}/users/me",
            headers=headers,
            json={"email": random_email()},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    await db.refresh(user)
    assert user.email == email


async def test_update_password_me(client: AsyncClient, db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    user = await crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    new_password = random_lower_string()
    data = {
        "current_password": password,
        "new_password": new_password,
    }
    r = await client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()
    assert updated_user["message"] == "Password updated successfully"

    await db.refresh(user)
    verified, _ = verify_password(new_password, user.hashed_password)
    assert verified

    assert (
        await client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    ).status_code == 401
    new_headers = await user_authentication_headers(
        client=client, email=email, password=new_password
    )
    assert (
        await client.get(f"{settings.API_V1_STR}/users/me", headers=new_headers)
    ).status_code == 200


async def test_update_password_me_incorrect_password(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    new_password = random_lower_string()
    data = {"current_password": new_password, "new_password": new_password}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 400
    updated_user = r.json()
    assert updated_user["detail"] == "Incorrect password"


async def test_update_user_me_email_exists(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)

    data = {"email": user.email}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/me",
        headers=normal_user_token_headers,
        json=data,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "User with this email already exists"


async def test_update_password_me_same_password_error(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {
        "current_password": settings.FIRST_SUPERUSER_PASSWORD,
        "new_password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = await client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 400
    updated_user = r.json()
    assert (
        updated_user["detail"] == "New password cannot be the same as the current one"
    )


async def test_register_user(client: AsyncClient, db: AsyncSession) -> None:
    username = random_email()
    password = random_lower_string()
    full_name = random_lower_string()
    data = {"email": username, "password": password, "full_name": full_name}
    r = await client.post(
        f"{settings.API_V1_STR}/users/signup",
        json=data,
    )
    assert r.status_code == 200
    created_user = r.json()
    assert created_user["email"] == username
    assert created_user["full_name"] == full_name

    user_query = select(User).where(User.email == username)
    user_db = (await db.exec(user_query)).first()
    assert user_db
    assert user_db.email == username
    assert user_db.full_name == full_name
    verified, _ = verify_password(password, user_db.hashed_password)
    assert verified


async def test_register_user_already_exists_error(client: AsyncClient) -> None:
    password = random_lower_string()
    full_name = random_lower_string()
    data = {
        "email": settings.FIRST_SUPERUSER,
        "password": password,
        "full_name": full_name,
    }
    r = await client.post(
        f"{settings.API_V1_STR}/users/signup",
        json=data,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "User with this email already exists"


async def test_concurrent_signups_for_same_email(client: AsyncClient) -> None:
    url = f"{settings.API_V1_STR}/users/signup"
    payload = {"email": random_email(), "password": random_lower_string()}
    results = await asyncio.gather(*(client.post(url, json=payload) for _ in range(2)))

    assert sorted(result.status_code for result in results) == [200, 409]


async def test_concurrent_updates_to_same_email(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    users = [await create_random_user(db), await create_random_user(db)]
    payload = {"email": random_email()}
    results = await asyncio.gather(
        *(
            client.patch(
                f"{settings.API_V1_STR}/users/{user.id}",
                json=payload,
                headers=superuser_token_headers,
            )
            for user in users
        )
    )

    assert sorted(result.status_code for result in results) == [200, 409]


async def test_partial_user_update_validation(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    user = await crud.create_user(
        session=db,
        user_create=UserCreate(
            email=random_email(), password=random_lower_string(), full_name="Name"
        ),
    )
    url = f"{settings.API_V1_STR}/users/{user.id}"
    for field in ("email", "password", "is_active", "is_superuser"):
        response = await client.patch(
            url, json={field: None}, headers=superuser_token_headers
        )
        assert response.status_code == 422

    response = await client.patch(
        url,
        json={"full_name": None, "is_active": False},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["email"] == user.email
    assert response.json()["full_name"] is None
    assert response.json()["is_active"] is False


async def test_update_user(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)

    data = {"full_name": "Updated_full_name"}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()

    assert updated_user["full_name"] == "Updated_full_name"

    user_query = select(User).where(User.email == username)
    user_db = (await db.exec(user_query)).first()
    await db.refresh(user_db)
    assert user_db
    assert user_db.full_name == "Updated_full_name"


async def test_update_user_not_exists(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"full_name": "Updated_full_name"}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "The user with this id does not exist in the system"


async def test_update_user_email_exists(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)

    username2 = random_email()
    password2 = random_lower_string()
    user_in2 = UserCreate(email=username2, password=password2)
    user2 = await crud.create_user(session=db, user_create=user_in2)

    data = {"email": user2.email}
    r = await client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "User with this email already exists"


@pytest.fixture
async def sole_active_superuser(db: AsyncSession) -> AsyncIterator[User]:
    user = await crud.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    assert user is not None and user.is_active and user.is_superuser
    other_ids = (
        (
            await db.exec(
                update(User)
                .where(
                    col(User.id) != user.id,
                    col(User.is_superuser).is_(True),
                    col(User.is_active).is_(True),
                )
                .values(is_superuser=False)
                .returning(col(User.id))
            )
        )
        .scalars()
        .all()
    )
    await db.commit()
    try:
        yield user
    finally:
        await db.refresh(user)
        user.is_superuser = True
        user.is_active = True
        if other_ids:
            await db.exec(
                update(User)
                .where(col(User.id).in_(other_ids))
                .values(is_superuser=True)
            )
        await db.commit()


@pytest.mark.parametrize("field", ["is_superuser", "is_active"])
async def test_cannot_remove_last_active_superuser(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    sole_active_superuser: User,
    field: str,
) -> None:
    url = f"{settings.API_V1_STR}/users/{sole_active_superuser.id}"
    response = await client.patch(
        url, headers=superuser_token_headers, json={field: False}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Cannot remove the last active superuser"}
    listing = await client.get(
        f"{settings.API_V1_STR}/users/", headers=superuser_token_headers
    )
    assert listing.status_code == 200


@pytest.mark.parametrize(
    ("isolation_level", "conflict_detail"),
    [
        ("READ COMMITTED", "Cannot remove the last active superuser"),
        ("REPEATABLE READ", "Superuser membership changed. Retry the request."),
    ],
)
async def test_concurrent_superuser_removals_preserve_one_admin(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    sole_active_superuser: User,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    isolation_level: str,
    conflict_detail: str,
) -> None:
    email, password = random_email(), random_lower_string()
    second = await crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, is_superuser=True),
    )
    second_headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    request_engine = engine.execution_options(isolation_level=isolation_level)
    both_ready = asyncio.Event()
    ready_count = 0
    acquire_lock = crud.lock_active_superuser_removal

    def create_request_session() -> AsyncSession:
        return AsyncSession(request_engine, expire_on_commit=False)

    async def wait_for_both_requests(session: AsyncSession) -> None:
        # Both requests must finish reading the target before either can write.
        nonlocal ready_count
        ready_count += 1
        if ready_count == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5)
        await acquire_lock(session)

    monkeypatch.setattr("app.api.deps.create_session", create_request_session)
    monkeypatch.setattr(crud, "lock_active_superuser_removal", wait_for_both_requests)
    try:
        responses = await asyncio.gather(
            client.patch(
                f"{settings.API_V1_STR}/users/{sole_active_superuser.id}",
                headers=superuser_token_headers,
                json={"is_superuser": False},
            ),
            client.patch(
                f"{settings.API_V1_STR}/users/{second.id}",
                headers=second_headers,
                json={"is_active": False},
            ),
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict = next(
            response for response in responses if response.status_code == 409
        )
        assert conflict.json() == {"detail": conflict_detail}
        active_ids = (
            await db.exec(
                select(User.id).where(
                    col(User.is_active).is_(True), col(User.is_superuser).is_(True)
                )
            )
        ).all()
        assert len(active_ids) == 1
    finally:
        await db.delete(second)
        await db.commit()


async def test_delete_user_me(client: AsyncClient, db: AsyncSession) -> None:
    import re

    from sqlalchemy import event

    from app.core.db import engine
    from app.models import Item

    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id
    db.add_all(Item(title=f"Item {index}", owner_id=user_id) for index in range(3))
    await db.commit()

    login_data = {
        "username": username,
        "password": password,
    }
    r = await client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    a_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {a_token}"}

    item_statements: list[str] = []

    def capture_item_queries(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if re.search(r"\bFROM item\b", statement, flags=re.IGNORECASE):
            item_statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_item_queries)
    try:
        r = await client.delete(
            f"{settings.API_V1_STR}/users/me",
            headers=headers,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_item_queries)
    assert r.status_code == 200
    deleted_user = r.json()
    assert deleted_user["message"] == "User deleted successfully"
    result = (await db.exec(select(User).where(User.id == user_id))).first()
    assert result is None

    assert not item_statements
    assert not (await db.exec(select(Item).where(Item.owner_id == user_id))).all()


async def test_delete_user_me_as_superuser(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    r = await client.delete(
        f"{settings.API_V1_STR}/users/me",
        headers=superuser_token_headers,
    )
    assert r.status_code == 403
    response = r.json()
    assert response["detail"] == "Super users are not allowed to delete themselves"


async def test_delete_user_super_user(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)
    user_id = user.id
    r = await client.delete(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200
    deleted_user = r.json()
    assert deleted_user["message"] == "User deleted successfully"
    result = (await db.exec(select(User).where(User.id == user_id))).first()
    assert result is None


async def test_delete_user_not_found(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    r = await client.delete(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "User not found"


async def test_delete_user_current_super_user_error(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    super_user = await crud.get_user_by_email(
        session=db, email=settings.FIRST_SUPERUSER
    )
    assert super_user
    user_id = super_user.id

    r = await client.delete(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Super users are not allowed to delete themselves"


async def test_delete_user_without_privileges(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = await crud.create_user(session=db, user_create=user_in)

    r = await client.delete(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"


async def test_pending_invitation_activates_only_on_acceptance(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    from app.email_outbox import get_cipher

    email = random_email()
    with (
        patch.object(settings, "SMTP_HOST", "smtp.example.com"),
        patch.object(settings, "EMAILS_FROM_EMAIL", "from@example.com"),
    ):
        response = await client.post(
            f"{settings.API_V1_STR}/users/invite",
            headers=superuser_token_headers,
            json={"email": email},
        )
    assert response.status_code == 201, response.json()
    user = response.json()
    assert user["invitation_pending"] and not user["is_active"]

    user_url = f"{settings.API_V1_STR}/users/{user['id']}"
    for changes in (
        {"is_active": True},
        {"email": random_email()},
        {"password": random_lower_string()},
    ):
        response = await client.patch(
            user_url, headers=superuser_token_headers, json=changes
        )
        assert response.status_code == 409

    response = await client.patch(
        user_url, headers=superuser_token_headers, json={"full_name": "Invited User"}
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Invited User"

    job = (
        await db.exec(select(EmailOutbox).where(EmailOutbox.recipient == email))
    ).one()
    assert job.payload and job.key_version
    token = get_cipher(job.key_version).decrypt(job.payload).decode()
    password = random_lower_string()
    for expected_status in (200, 400):
        response = await client.post(
            f"{settings.API_V1_STR}/invitations/accept",
            json={"token": token, "new_password": password},
        )
        assert response.status_code == expected_status
    response = await client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200


async def test_invites_require_email_without_changing_recovery_response(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    response = await client.post(
        f"{settings.API_V1_STR}/users/invite",
        headers=superuser_token_headers,
        json={"email": random_email()},
    )
    assert response.status_code == 503
    results = [
        await client.post(
            f"{settings.API_V1_STR}/password-recovery", json={"email": email}
        )
        for email in (settings.FIRST_SUPERUSER, random_email())
    ]
    assert all(result.status_code == 200 for result in results)
    assert results[0].json() == results[1].json()
