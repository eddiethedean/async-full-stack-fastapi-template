import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import User
from tests.utils.utils import random_email

pytestmark = pytest.mark.anyio


async def test_test_mode_is_excluded_from_openapi(client: AsyncClient) -> None:
    path = f"{settings.API_V1_STR}/private/test-mode"
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json() == {"test_mode": True}

    schema = await client.get(f"{settings.API_V1_STR}/openapi.json")
    assert path not in schema.json()["paths"]


async def test_create_user(client: AsyncClient, db: AsyncSession) -> None:
    r = await client.post(
        f"{settings.API_V1_STR}/private/users/",
        json={
            "email": "pollo@LISTO.COM",
            "password": "password123",
            "full_name": "Pollo Listo",
        },
    )

    assert r.status_code == 200

    data = r.json()

    user = (await db.exec(select(User).where(User.id == data["id"]))).first()

    assert user
    assert user.email == "pollo@listo.com"
    assert user.full_name == "Pollo Listo"


async def test_create_user_reports_duplicate_without_overwriting_fixture(
    client: AsyncClient,
) -> None:
    path = f"{settings.API_V1_STR}/private/users/"
    email = random_email()
    password = "original-fixture-password"
    created = await client.post(
        path,
        json={"email": email, "password": password, "full_name": "Original Fixture"},
    )
    assert created.status_code == 200

    duplicate = await client.post(
        path,
        json={
            "email": email,
            "password": "different-fixture-password",
            "full_name": "Different Fixture",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "User with this email already exists"}

    login = await client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    account = await client.get(
        f"{settings.API_V1_STR}/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert account.status_code == 200
    assert account.json() == created.json()
    schema = await client.get(f"{settings.API_V1_STR}/openapi.json")
    assert path not in schema.json()["paths"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "invalid-email-fixture"),
        ("password", "short"),
        ("full_name", "x" * 256),
    ],
)
async def test_create_user_rejects_invalid_fixture(
    client: AsyncClient,
    db: AsyncSession,
    superuser_token_headers: dict[str, str],
    field: str,
    value: str,
) -> None:
    data = {
        "email": random_email(),
        "password": "test-fixture-123",
        "full_name": "Fixture",
    }
    data[field] = value

    response = await client.post(f"{settings.API_V1_STR}/private/users/", json=data)

    assert response.status_code == 422
    assert (
        await db.exec(select(User).where(User.email == data["email"]))
    ).first() is None
    response = await client.get(
        f"{settings.API_V1_STR}/users/", headers=superuser_token_headers
    )
    assert response.status_code == 200
