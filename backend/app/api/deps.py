from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie, OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import security
from app.core.browser_auth import COOKIE_NAME, SAFE_METHODS, SESSION_HEADER, check_csrf
from app.core.config import settings
from app.core.db import create_session
from app.core.sessions import token_digest
from app.models import AuthSession, User, get_datetime_utc

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token", auto_error=False
)
browser_session_cookie = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with create_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]
TokenDep = Annotated[str | None, Depends(reusable_oauth2)]
BrowserCookieDep = Annotated[str | None, Depends(browser_session_cookie)]


def get_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


async def get_bearer_session(
    session: AsyncSession, token: str
) -> tuple[AuthSession, User]:
    try:
        claims = security.decode_access_token(token)
    except InvalidTokenError, ValidationError:
        raise get_credentials_error()
    auth_session = await session.get(AuthSession, claims.sid)
    if (
        auth_session is None
        or auth_session.kind != "bearer"
        or auth_session.user_id != claims.sub
    ):
        raise get_credentials_error()
    return auth_session, await get_session_user(session, auth_session)


async def get_session_user(session: AsyncSession, auth_session: AuthSession) -> User:
    if (
        auth_session.revoked_at is not None
        or auth_session.expires_at <= get_datetime_utc()
    ):
        raise get_credentials_error()
    user = await session.get(User, auth_session.user_id)
    if (
        user is None
        or not user.is_active
        or user.auth_version != auth_session.auth_version
    ):
        raise get_credentials_error()
    return user


async def find_browser_session(
    session: AsyncSession, credential: str | None
) -> AuthSession | None:
    if not credential:
        return None
    statement = select(AuthSession).where(
        col(AuthSession.kind) == "browser",
        col(AuthSession.cookie_digest) == token_digest(credential),
    )
    return (await session.exec(statement)).one_or_none()


def check_browser_session_binding(request: Request, auth_session: AuthSession) -> None:
    expected = request.headers.get(SESSION_HEADER)
    if expected is not None and expected != str(auth_session.id):
        raise get_credentials_error()


@dataclass
class AuthenticatedSession:
    session: AuthSession
    user: User


async def get_browser_session(
    session: SessionDep, cookie: BrowserCookieDep, request: Request
) -> AuthenticatedSession:
    if "authorization" in request.headers:
        raise get_credentials_error()
    auth_session = await find_browser_session(session, cookie)
    if auth_session is None:
        raise get_credentials_error()
    user = await get_session_user(session, auth_session)
    return AuthenticatedSession(auth_session, user)


async def get_authenticated_session(
    session: SessionDep,
    token: TokenDep,
    cookie: BrowserCookieDep,
    request: Request,
    response: Response,
) -> AuthenticatedSession:
    response.headers["Cache-Control"] = "no-store"
    if "authorization" in request.headers:
        if not token:
            raise get_credentials_error()
        auth_session, user = await get_bearer_session(session, token)
        return AuthenticatedSession(auth_session, user)
    browser = await get_browser_session(session, cookie, request)
    check_browser_session_binding(request, browser.session)
    if request.method not in SAFE_METHODS:
        check_csrf(request, browser.session)
    return browser


AuthDep = Annotated[AuthenticatedSession, Depends(get_authenticated_session)]
BrowserAuthDep = Annotated[AuthenticatedSession, Depends(get_browser_session)]


def get_current_user(auth: AuthDep) -> User:
    return auth.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user
