import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import settings
from app.core.db import create_session
from app.core.rate_limits import (
    RateLimitBucket,
    check_rate_limits,
    client_address,
    purge_rate_limit_buckets,
)
from app.crud import create_user
from app.models import UserCreate

pytestmark = pytest.mark.anyio


async def test_shared_limit_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_SIGNUP_ACCOUNT", 2)
    email = f"{uuid4().hex}@example.com"
    results = await asyncio.gather(
        *(check_rate_limits("signup", email=email, source=str(i)) for i in range(5))
    )
    assert results.count(None) == 2
    assert all(
        0 < delay <= settings.RATE_LIMIT_WINDOW_SECONDS for delay in results if delay
    )


async def test_source_limit_is_separate_from_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_RECOVERY_SOURCE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_RECOVERY_ACCOUNT", 1)
    source = uuid4().hex
    assert (
        await check_rate_limits("recovery", email="a@example.com", source=source)
        is None
    )
    assert await check_rate_limits("recovery", email="b@example.com", source=source)
    assert (
        await check_rate_limits("recovery", email="b@example.com", source="other")
        is None
    )


async def test_limited_login_stops_before_password_verification(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_ACCOUNT", 1)
    email = f"{uuid4().hex}@example.com"
    assert await check_rate_limits("login", email=email, source=uuid4().hex) is None

    async def unexpected_authenticate(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Password verification ran after rejecting the request")

    monkeypatch.setattr("app.crud.authenticate", unexpected_authenticate)
    response = await client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": email, "password": "example-password"},
    )
    assert response.status_code == 429
    assert (
        0 < int(response.headers["Retry-After"]) <= settings.RATE_LIMIT_WINDOW_SECONDS
    )


async def test_cleanup_removes_only_expired_buckets() -> None:
    start = datetime.now(UTC)
    key_hash = uuid4().hex
    async with create_session() as session, session.begin():
        for seconds in (-3600, 3600):
            session.add(
                RateLimitBucket(
                    action="login",
                    kind="account",
                    key_hash=key_hash,
                    window=start + timedelta(seconds=seconds - 300),
                    expires_at=start + timedelta(seconds=seconds),
                    count=1,
                )
            )
    async with create_session() as session, session.begin():
        await purge_rate_limit_buckets(session)
        rows = (
            await session.exec(
                select(RateLimitBucket).where(col(RateLimitBucket.key_hash) == key_hash)
            )
        ).all()
        assert len(rows) == 1 and rows[0].expires_at > start


async def test_client_address_ignores_headers_from_untrusted_peers() -> None:
    seen: list[str] = []

    async def capture(scope, _receive, _send):
        seen.append(client_address(Request(scope)))

    for peer in ("192.0.2.9", "192.0.2.10"):
        scope = {
            "type": "http",
            "scheme": "http",
            "client": (peer, 8080),
            "headers": [(b"x-forwarded-for", b"203.0.113.8")],
        }
        await ProxyHeadersMiddleware(capture, trusted_hosts="192.0.2.9")(
            scope, None, None
        )

    assert seen == ["203.0.113.8", "192.0.2.10"]


@pytest.mark.parametrize("known_account", [False, True])
async def test_cookie_and_bearer_login_share_the_account_limit(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    known_account: bool,
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_ACCOUNT", 1)
    local_part = uuid4().hex
    email = f"{local_part}@example.com"
    credentials = {"username": email, "password": "test-only-password"}
    if known_account:
        await create_user(
            session=db,
            user_create=UserCreate(email=email, password=credentials["password"]),
        )
    response = await client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={**credentials, "username": f"{local_part}@EXAMPLE.COM"},
    )
    assert response.status_code == (200 if known_account else 400)
    response = await client.post(
        f"{settings.API_V1_STR}/auth/session",
        json=credentials,
        headers={"Origin": settings.auth_origins[0]},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"].isdigit()


async def test_limiter_reports_unavailable_datastore(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    async def unavailable(*_args: object, **_kwargs: object) -> int | None:
        raise SQLAlchemyError()

    monkeypatch.setattr("app.core.rate_limits.check_rate_limits", unavailable)
    response = await client.post(
        f"{settings.API_V1_STR}/password-recovery", json={"email": "reader@example.com"}
    )
    assert response.status_code == 503
