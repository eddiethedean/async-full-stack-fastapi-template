from collections.abc import AsyncIterator
from typing import Annotated

import anyio
import pytest
from anyio.lowlevel import checkpoint
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import literal
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import SessionDep
from app.core.db import engine


@pytest.mark.anyio
async def test_cancelled_request_returns_database_connection() -> None:
    app = FastAPI()
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    initial_connections = pool.checkedout()
    query_finished = anyio.Event()
    cleanup_finished = anyio.Event()

    async def track_cleanup(session: SessionDep) -> AsyncIterator[AsyncSession]:
        try:
            yield session
        finally:
            # Exercise an await during teardown while the request is cancelled.
            await checkpoint()
            cleanup_finished.set()

    @app.get("/")
    async def hold_connection(
        session: Annotated[AsyncSession, Depends(track_cleanup, scope="function")],
    ) -> None:
        await session.exec(select(literal(1)))
        query_finished.set()
        await anyio.sleep_forever()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(client.get, "/")
                await query_finished.wait()
                assert pool.checkedout() == initial_connections + 1
                tasks.cancel_scope.cancel()

        assert cleanup_finished.is_set()
        assert pool.checkedout() == initial_connections
