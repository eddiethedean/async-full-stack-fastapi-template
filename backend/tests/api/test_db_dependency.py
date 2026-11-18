import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import literal
from sqlmodel import select

from app.api.deps import SessionDep
from app.core.db import engine


@pytest.mark.anyio
async def test_cancelled_request_returns_database_connection() -> None:
    app = FastAPI()
    checked_out = engine.pool.checkedout  # type: ignore[attr-defined]
    initial_connections = checked_out()
    query_finished = anyio.Event()

    @app.get("/")
    async def hold_connection(session: SessionDep) -> None:
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
                assert checked_out() == initial_connections + 1
                tasks.cancel_scope.cancel()

        # The driver can finish returning the connection in its cleanup task.
        with anyio.fail_after(5):
            while checked_out() != initial_connections:
                await anyio.sleep(0.01)
