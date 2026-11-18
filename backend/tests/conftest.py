import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.database import test_database_url


def pytest_configure() -> None:
    try:
        os.environ["DATABASE_URL"] = test_database_url()
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    os.environ.update(FASTAPI_ENV="development", ENABLE_TEST_ROUTES="true")


@pytest.fixture(scope="session")
def anyio_backend() -> tuple[str, dict[str, object]]:
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture(scope="session", autouse=True)
async def prepare_db(anyio_backend: object) -> AsyncIterator[None]:  # noqa: ARG001
    from app.core.db import create_session, init_db
    from app.main import app

    async with app.router.lifespan_context(app):
        async with create_session() as session:
            await init_db(session)
        yield


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    from app.core.db import create_session

    async with create_session() as session:
        yield session


@pytest.fixture(scope="module")
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


@pytest.fixture
async def superuser_token_headers(client: AsyncClient) -> dict[str, str]:
    from tests.utils.utils import get_superuser_token_headers

    return await get_superuser_token_headers(client)


@pytest.fixture
async def normal_user_token_headers(client: AsyncClient) -> dict[str, str]:
    from app.core.config import settings
    from app.core.db import create_session
    from tests.utils.user import authentication_token_from_email

    async with create_session() as session:
        return await authentication_token_from_email(
            client=client, email=settings.EMAIL_TEST_USER, db=session
        )
