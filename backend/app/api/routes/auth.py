from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import Field, SQLModel

from app import crud
from app.api.deps import (
    AuthDep,
    BrowserAuthDep,
    BrowserCookieDep,
    SessionDep,
    find_browser_session,
    get_credentials_error,
    get_session_user,
)
from app.core.browser_auth import (
    check_csrf,
    check_origin,
    clear_session_cookie,
    csrf_token,
    set_session_cookie,
)
from app.core.config import settings
from app.core.rate_limits import enforce_rate_limit
from app.core.sessions import (
    generate_opaque_token,
    lock_user,
    revoke_user_sessions,
    token_digest,
)
from app.models import AuthSession, User, UserPublic, get_datetime_utc

router = APIRouter(prefix="/auth", tags=["auth"])


class BrowserLogin(SQLModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class BrowserSession(SQLModel):
    user: UserPublic
    expires_at: datetime
    csrf_token: str


def _session_info(auth_session: AuthSession, user: User) -> BrowserSession:
    return BrowserSession(
        user=UserPublic.model_validate(user),
        expires_at=auth_session.expires_at,
        csrf_token=csrf_token(auth_session),
    )


@router.post("/session")
async def create_browser_session(
    body: BrowserLogin,
    session: SessionDep,
    cookie: BrowserCookieDep,
    request: Request,
    response: Response,
) -> BrowserSession:
    """Sign in and create an HttpOnly browser session."""
    check_origin(request)
    if "authorization" in request.headers:
        raise get_credentials_error()
    await enforce_rate_limit(request, action="login", email=body.username)
    user = await crud.authenticate(
        session=session, email=body.username, password=body.password, commit=False
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
            headers={"Cache-Control": "no-store"},
        )

    old_session = await find_browser_session(session, cookie)
    now = get_datetime_utc()
    if old_session is not None and old_session.revoked_at is None:
        old_session.revoked_at = now
    credential = generate_opaque_token()
    auth_session = AuthSession(
        user_id=user.id,
        kind="browser",
        auth_version=user.auth_version,
        cookie_digest=token_digest(credential),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.BROWSER_SESSION_EXPIRE_MINUTES),
    )
    session.add(auth_session)
    await session.commit()

    response.headers["Cache-Control"] = "no-store"
    set_session_cookie(response, credential, auth_session)
    return _session_info(auth_session, user)


@router.get("/session")
async def read_browser_session(
    auth: BrowserAuthDep, response: Response
) -> BrowserSession:
    """Restore browser identity and receive the CSRF token for writes."""
    response.headers["Cache-Control"] = "no-store"
    return _session_info(auth.session, auth.user)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_browser_session(
    session: SessionDep,
    cookie: BrowserCookieDep,
    request: Request,
    response: Response,
) -> None:
    """Revoke the active browser session and clear its cookie."""
    check_origin(request)
    if "authorization" in request.headers:
        raise get_credentials_error()

    auth_session = await find_browser_session(session, cookie)
    if auth_session is not None:
        try:
            await get_session_user(session, auth_session)
        except HTTPException as error:
            if error.status_code != status.HTTP_401_UNAUTHORIZED:
                raise
        else:
            check_csrf(request, auth_session)
            auth_session.revoked_at = get_datetime_utc()
            await session.commit()

    response.headers["Cache-Control"] = "no-store"
    clear_session_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(session: SessionDep, auth: AuthDep, response: Response) -> None:
    """Revoke every session for the signed-in account."""
    user = await lock_user(session, auth.user.id)
    if user is None:
        raise get_credentials_error()
    await session.refresh(auth.session)
    await get_session_user(session, auth.session)
    await revoke_user_sessions(session, user)
    await session.commit()

    response.headers["Cache-Control"] = "no-store"
    if auth.session.kind == "browser":
        clear_session_cookie(response)
