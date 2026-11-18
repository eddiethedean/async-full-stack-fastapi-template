import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import User

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
            "email": "pollo@listo.com",
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
