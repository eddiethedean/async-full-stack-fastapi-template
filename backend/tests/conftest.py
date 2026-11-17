from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.main import app
from app.models import Item, User
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def prepare_db(anyio_backend: str) -> AsyncIterator[None]:  # noqa: ARG001
    async with app.router.lifespan_context(app):
        async with SessionLocal() as session:
            await init_db(session)
        yield
        async with SessionLocal() as session:
            await session.exec(delete(Item))
            await session.exec(delete(User))
            await session.commit()


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@pytest.fixture(scope="module")
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


@pytest.fixture(scope="module")
async def superuser_token_headers(client: AsyncClient) -> dict[str, str]:
    return await get_superuser_token_headers(client)


@pytest.fixture(scope="module")
async def normal_user_token_headers(client: AsyncClient) -> dict[str, str]:
    async with SessionLocal() as session:
        return await authentication_token_from_email(
            client=client, email=settings.EMAIL_TEST_USER, db=session
        )
