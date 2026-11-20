import asyncio
from datetime import timedelta
from email.message import EmailMessage
from unittest.mock import patch
from uuid import UUID

import jwt
import pytest
from httpx import AsyncClient, Response
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.config import settings
from app.core.db import create_session
from app.core.security import (
    ALGORITHM,
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.core.sessions import AccountAction, issue_account_action, lock_user
from app.crud import create_user
from app.email_outbox import EmailOutbox, get_cipher
from app.models import AuthSession, User, UserCreate, get_datetime_utc
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


async def test_get_access_token(client: AsyncClient) -> None:
    login_data = {
        "username": settings.FIRST_SUPERUSER,
        "password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = await client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    assert r.status_code == 200
    assert "access_token" in tokens
    assert tokens["access_token"]


async def test_get_access_token_incorrect_password(client: AsyncClient) -> None:
    login_data = {
        "username": settings.FIRST_SUPERUSER,
        "password": "incorrect",
    }
    r = await client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    assert r.status_code == 400


async def test_use_access_token(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    r = await client.post(
        f"{settings.API_V1_STR}/login/test-token",
        headers=superuser_token_headers,
    )
    result = r.json()
    assert r.status_code == 200
    assert "email" in result


@pytest.mark.parametrize(
    "claim_error",
    [
        "missing_token",
        "invalid_token",
        "missing_exp",
        "invalid_sub",
        "expired",
        "deleted_user",
    ],
)
async def test_unusable_access_credentials(
    client: AsyncClient, db: AsyncSession, claim_error: str
) -> None:
    headers = {}
    if claim_error != "missing_token":
        user = await create_user(
            session=db,
            user_create=UserCreate(
                email=random_email(), password=random_lower_string()
            ),
        )
        expiration = get_datetime_utc() + timedelta(minutes=1)
        auth_session = AuthSession(
            user_id=user.id,
            kind="bearer",
            auth_version=user.auth_version,
            expires_at=expiration,
        )
        db.add(auth_session)
        await db.commit()
        claims = jwt.decode(
            create_access_token(auth_session), options={"verify_signature": False}
        )
        if claim_error == "missing_exp":
            del claims["exp"]
        elif claim_error == "invalid_sub":
            claims["sub"] = "not-a-uuid"
        elif claim_error == "expired":
            claims["exp"] = expiration - timedelta(minutes=2)
        elif claim_error == "deleted_user":
            await db.delete(user)
            await db.commit()
        token = (
            "invalid"
            if claim_error == "invalid_token"
            else jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM)
        )
        headers["Authorization"] = f"Bearer {token}"

    response = await client.get(f"{settings.API_V1_STR}/users/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("legacy", [False, True])
async def test_recovery_password(
    client: AsyncClient, db: AsyncSession, legacy: bool
) -> None:
    email = random_email()
    await create_user(
        session=db, user_create=UserCreate(email=email, password=random_lower_string())
    )
    local_part, domain = email.rsplit("@", 1)
    submitted_email = f"{local_part}@{domain.upper()}"
    path = f"{settings.API_V1_STR}/password-recovery"
    with (
        patch.object(settings, "SMTP_HOST", "smtp.example.com"),
        patch.object(settings, "EMAILS_FROM_EMAIL", "from@example.com"),
    ):
        if legacy:
            response = await client.post(f"{path}/{submitted_email}")
        else:
            response = await client.post(path, json={"email": submitted_email})
    assert response.status_code == 200
    assert response.json() == {
        "message": "If that email is registered, we sent a password recovery link"
    }
    job = (
        await db.exec(
            select(EmailOutbox)
            .where(col(EmailOutbox.recipient) == email)
            .order_by(col(EmailOutbox.created_at).desc())
        )
    ).first()
    assert job and job.kind == "password_reset" and job.payload


@pytest.mark.parametrize("local_part_prefix", ["", "田中"])
async def test_recovery_html_preview_subject(
    client: AsyncClient,
    db: AsyncSession,
    superuser_token_headers: dict[str, str],
    local_part_prefix: str,
) -> None:
    email = f"{local_part_prefix}{random_email()}"
    await create_user(
        session=db, user_create=UserCreate(email=email, password=random_lower_string())
    )

    response = await client.post(
        f"{settings.API_V1_STR}/password-recovery-html-content/{email}",
        headers=superuser_token_headers,
    )

    assert response.status_code == 200
    assert email in response.text
    assert "preview-only" in response.text
    subject = response.headers["subject"]
    assert subject.isascii()
    assert "\r" not in subject and "\n" not in subject
    expected_subject = f"{settings.PROJECT_NAME} - Password recovery for user {email}"
    if not local_part_prefix:
        assert subject == expected_subject
    message = EmailMessage()
    message["Subject"] = subject
    assert str(message["Subject"]) == expected_subject


async def test_recovery_password_user_not_exits(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    email = "jVgQr@example.com"
    r = await client.post(
        f"{settings.API_V1_STR}/password-recovery/{email}",
        headers=normal_user_token_headers,
    )
    # Should return 200 with generic message to prevent email enumeration attacks
    assert r.status_code == 200
    assert r.json() == {
        "message": "If that email is registered, we sent a password recovery link"
    }


async def test_reset_password(client: AsyncClient, db: AsyncSession) -> None:
    email = random_email()
    password = random_lower_string()
    new_password = random_lower_string()

    user_create = UserCreate(
        email=email,
        full_name="Test User",
        password=password,
        is_active=True,
        is_superuser=False,
    )
    user = await create_user(session=db, user_create=user_create)
    token = issue_account_action(
        db, user, purpose="password_reset", expires_delta=timedelta(hours=1)
    )
    await db.commit()
    headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    data = {"new_password": new_password, "token": token}

    responses = await asyncio.gather(
        client.post(f"{settings.API_V1_STR}/reset-password/", json=data),
        client.post(f"{settings.API_V1_STR}/reset-password/", json=data),
    )
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert next(r.json() for r in responses if r.status_code == 400) == {
        "detail": "Invalid token"
    }
    expired_session = await client.get(
        f"{settings.API_V1_STR}/users/me", headers=headers
    )
    assert expired_session.status_code == 401

    await db.refresh(user)
    verified, _ = verify_password(new_password, user.hashed_password)
    assert verified
    new_headers = await user_authentication_headers(
        client=client, email=email, password=new_password
    )
    assert (
        await client.get(f"{settings.API_V1_STR}/users/me", headers=new_headers)
    ).status_code == 200


async def test_reset_can_retry_after_rolled_back_write(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await create_user(
        session=db,
        user_create=UserCreate(email=random_email(), password=random_lower_string()),
    )
    token = issue_account_action(
        db, user, purpose="password_reset", expires_delta=timedelta(hours=1)
    )
    await db.commit()
    payload = {"token": token, "new_password": random_lower_string()}
    url = f"{settings.API_V1_STR}/reset-password/"

    with (
        patch("app.api.routes.login.crud.set_password", side_effect=RuntimeError),
        pytest.raises(RuntimeError),
    ):
        await client.post(url, json=payload)
    assert (await client.post(url, json=payload)).status_code == 200


@pytest.mark.parametrize(
    ("purpose", "ttl"),
    [("invite", timedelta(hours=1)), ("password_reset", timedelta(hours=-1))],
)
async def test_reset_rejects_unusable_action_links(
    client: AsyncClient, db: AsyncSession, purpose: AccountAction, ttl: timedelta
) -> None:
    user = await create_user(
        session=db,
        user_create=UserCreate(email=random_email(), password=random_lower_string()),
    )
    token = issue_account_action(db, user, purpose=purpose, expires_delta=ttl)
    await db.commit()
    response = await client.post(
        f"{settings.API_V1_STR}/reset-password/",
        json={"token": token, "new_password": random_lower_string()},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid token"}


async def test_login_rechecks_credentials_after_verification(
    client: AsyncClient, db: AsyncSession
) -> None:
    email = random_email()
    old_password, new_password = random_lower_string(), random_lower_string()
    user = await create_user(
        session=db, user_create=UserCreate(email=email, password=old_password)
    )
    new_hash = get_password_hash(new_password)

    async def change_credentials_before_login_locks(
        login_session: AsyncSession, user_id: UUID
    ) -> User | None:
        async with create_session() as change_session:
            changing_user = await lock_user(change_session, user_id)
            assert changing_user is not None
            await crud.set_password(change_session, changing_user, new_hash)
            await change_session.commit()
        return await lock_user(login_session, user_id)

    with patch("app.crud.lock_user", side_effect=change_credentials_before_login_locks):
        stale_login = await client.post(
            f"{settings.API_V1_STR}/login/access-token",
            data={"username": email, "password": old_password},
        )
    assert stale_login.status_code == 400
    await db.refresh(user)
    assert user.hashed_password == new_hash
    await user_authentication_headers(client=client, email=email, password=new_password)


async def test_reset_password_invalid_token(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"new_password": "changethis", "token": "invalid"}
    r = await client.post(
        f"{settings.API_V1_STR}/reset-password/",
        headers=superuser_token_headers,
        json=data,
    )
    response = r.json()

    assert "detail" in response
    assert r.status_code == 400
    assert response["detail"] == "Invalid token"


@pytest.mark.parametrize("browser", [False, True], ids=["bearer", "browser"])
async def test_long_multibyte_argon2_password_signup_login_and_reset(
    client: AsyncClient, db: AsyncSession, browser: bool
) -> None:
    password = "🌳" * 19
    api = settings.API_V1_STR
    email = random_email()
    signup = await client.post(
        f"{api}/users/signup", json={"email": email, "password": password}
    )
    assert signup.status_code == 200
    user = (await db.exec(select(User).where(col(User.email) == email))).one()
    assert user.hashed_password.startswith("$argon2id$")

    path = f"{api}/auth/session" if browser else f"{api}/login/access-token"
    headers = {"Origin": settings.auth_origins[0]}

    async def login(candidate: str) -> Response:
        credentials = {"username": email, "password": candidate}
        if browser:
            return await client.post(path, json=credentials, headers=headers)
        return await client.post(path, data=credentials)

    assert (await login(password)).status_code == 200
    # A candidate with the same first 72 UTF-8 bytes must still fail.
    wrong_password = "🌳" * 18 + "🌲"
    response = await login(wrong_password)
    assert response.status_code == 400
    assert response.json() == {"detail": "Incorrect email or password"}

    with (
        patch.object(settings, "SMTP_HOST", "smtp.example.com"),
        patch.object(settings, "EMAILS_FROM_EMAIL", "from@example.com"),
    ):
        recovery = await client.post(
            f"{api}/password-recovery", json={"email": user.email}
        )
    assert recovery.status_code == 200
    job = (
        await db.exec(select(EmailOutbox).where(col(EmailOutbox.user_id) == user.id))
    ).one()
    assert job.kind == "password_reset" and job.payload and job.key_version
    token = get_cipher(job.key_version).decrypt(job.payload).decode()
    new_password = "🌲" * 19
    reset = await client.post(
        f"{api}/reset-password/", json={"token": token, "new_password": new_password}
    )
    assert reset.status_code == 200
    await db.refresh(user)
    assert user.hashed_password.startswith("$argon2")

    response = await login(new_password)
    assert response.status_code == 200
    if browser:
        assert response.json()["user"]["email"] == email
    else:
        assert response.json()["access_token"]


async def test_login_with_argon2_password_keeps_hash(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Test that logging in with an argon2 password hash does not update it."""
    email = random_email()
    password = random_lower_string()

    # Create an argon2 hash (current default)
    argon2_hash = get_password_hash(password)
    assert argon2_hash.startswith("$argon2")

    # Create user with argon2 hash
    user = User(email=email, hashed_password=argon2_hash, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    original_hash = user.hashed_password

    login_data = {"username": email, "password": password}
    r = await client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    assert r.status_code == 200
    tokens = r.json()
    assert "access_token" in tokens

    await db.refresh(user)

    assert user.hashed_password == original_hash
    assert user.hashed_password.startswith("$argon2")
