from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.browser_auth import COOKIE_NAME, CSRF_HEADER, SESSION_HEADER
from app.core.config import settings
from app.crud import create_user
from app.main import app
from app.models import UserCreate
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio
session_url = f"{settings.API_V1_STR}/auth/session"
profile_url = f"{settings.API_V1_STR}/users/me"
trusted_origin = {"Origin": settings.auth_origins[0]}


@pytest.fixture
async def browser() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        yield client


@pytest.fixture
async def login_body(db: AsyncSession) -> dict[str, str]:
    email, password = random_email(), random_lower_string()
    await create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    return {"username": email, "password": password}


async def test_browser_restores_and_revokes_session(
    browser: AsyncClient, login_body: dict[str, str]
) -> None:
    assert (await browser.post(session_url, json=login_body)).status_code == 403
    login = await browser.post(session_url, json=login_body, headers=trusted_origin)
    assert login.status_code == 200
    assert login.headers["Cache-Control"] == "no-store"
    assert "access_token" not in login.json()
    cookie_header = login.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "path=/" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "domain=" not in cookie_header
    if settings.AUTH_COOKIE_SECURE:
        assert cookie_header.startswith("__host-")
        assert "secure" in cookie_header
    cookie = browser.cookies[COOKIE_NAME]
    restored = await browser.get(session_url)
    assert restored.json() == login.json()
    assert restored.headers["Cache-Control"] == "no-store"
    assert (await browser.get(profile_url)).status_code == 200

    assert (
        await browser.delete(session_url, headers=trusted_origin)
    ).status_code == 403
    headers = {**trusted_origin, CSRF_HEADER: restored.json()["csrf_token"]}
    signed_out = await browser.delete(session_url, headers=headers)
    assert signed_out.status_code == 204
    assert signed_out.headers["Cache-Control"] == "no-store"
    assert COOKIE_NAME not in browser.cookies
    assert (
        await browser.delete(session_url, headers=trusted_origin)
    ).status_code == 204

    browser.cookies.set(COOKIE_NAME, cookie)
    assert (await browser.get(profile_url)).status_code == 401


async def test_browser_writes_require_origin_and_session_csrf(
    browser: AsyncClient, login_body: dict[str, str]
) -> None:
    first = await browser.post(session_url, json=login_body, headers=trusted_origin)
    second = await browser.post(session_url, json=login_body, headers=trusted_origin)
    first_csrf = first.json()["csrf_token"]
    second_csrf = second.json()["csrf_token"]
    assert first_csrf != second_csrf
    for headers in (
        trusted_origin,
        {**trusted_origin, CSRF_HEADER: first_csrf},
        {"Origin": "https://different-origin.example", CSRF_HEADER: second_csrf},
    ):
        response = await browser.patch(
            profile_url, json={"full_name": "Changed"}, headers=headers
        )
        assert response.status_code == 403

    updated = await browser.patch(
        profile_url,
        json={"full_name": "Changed"},
        headers={**trusted_origin, CSRF_HEADER: second_csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["full_name"] == "Changed"


async def test_browser_reads_require_a_matching_session_when_bound(
    browser: AsyncClient, login_body: dict[str, str]
) -> None:
    first = await browser.post(session_url, json=login_body, headers=trusted_origin)
    second = await browser.post(session_url, json=login_body, headers=trusted_origin)
    current = {SESSION_HEADER: second.json()["session_id"]}
    previous = {SESSION_HEADER: first.json()["session_id"]}

    assert (await browser.get(profile_url, headers=current)).status_code == 200
    assert (await browser.get(profile_url, headers=previous)).status_code == 401
    restored = await browser.get(session_url, headers=previous)
    assert restored.json()["session_id"] == current[SESSION_HEADER]


@pytest.mark.parametrize("clear_cookie", [False, True])
async def test_browser_logout_can_revoke_without_expiring_the_cookie(
    browser: AsyncClient,
    login_body: dict[str, str],
    clear_cookie: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if clear_cookie:
        monkeypatch.setattr(
            settings, "AUTH_TRUSTED_ORIGINS", ["https://another-frontend.example"]
        )
    login = await browser.post(session_url, json=login_body, headers=trusted_origin)
    headers = {
        **trusted_origin,
        CSRF_HEADER: login.json()["csrf_token"],
        SESSION_HEADER: login.json()["session_id"],
    }
    response = await browser.delete(
        session_url, params={"clear_cookie": clear_cookie}, headers=headers
    )

    assert response.status_code == 204
    assert "set-cookie" not in response.headers
    assert (await browser.get(session_url)).status_code == 401


async def test_bearer_precedence_and_logout_all(
    browser: AsyncClient, login_body: dict[str, str]
) -> None:
    bearer = await user_authentication_headers(
        client=browser, email=login_body["username"], password=login_body["password"]
    )
    login = await browser.post(session_url, json=login_body, headers=trusted_origin)
    assert login.status_code == 200
    assert (
        await browser.get(profile_url, headers={"Authorization": "Basic invalid"})
    ).status_code == 401
    assert (
        await browser.patch(profile_url, json={"full_name": "Bearer"}, headers=bearer)
    ).status_code == 200

    response = await browser.post(
        f"{settings.API_V1_STR}/auth/logout-all",
        headers={**trusted_origin, CSRF_HEADER: login.json()["csrf_token"]},
    )
    assert response.status_code == 204
    assert (await browser.get(profile_url, headers=bearer)).status_code == 401
    assert (await browser.get(session_url)).status_code == 401
