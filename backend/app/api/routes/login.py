from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from starlette.concurrency import run_in_threadpool

from app import crud
from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core import security
from app.core.config import settings
from app.core.sessions import (
    consume_account_action,
    find_account_action_user_id,
    issue_account_action,
    lock_user,
)
from app.models import (
    AuthSession,
    Message,
    NewPassword,
    Token,
    UserPublic,
    get_datetime_utc,
)
from app.utils import generate_reset_password_email, send_email

router = APIRouter(tags=["login"])


@router.post("/login/access-token")
async def login_access_token(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    user = await crud.authenticate(
        session=session,
        email=form_data.username,
        password=form_data.password,
        commit=False,
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    now = get_datetime_utc()
    auth_session = AuthSession(
        user_id=user.id,
        kind="bearer",
        auth_version=user.auth_version,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    session.add(auth_session)
    await session.commit()
    return Token(access_token=security.create_access_token(auth_session))


@router.post("/login/test-token", response_model=UserPublic)
def test_token(current_user: CurrentUser) -> Any:
    """
    Test access token
    """
    return current_user


@router.post("/password-recovery/{email}")
async def recover_password(email: str, session: SessionDep) -> Message:
    """
    Password Recovery
    """
    user = (
        await crud.get_user_by_email(session=session, email=email)
        if settings.emails_enabled
        else None
    )

    # Always return the same response to prevent email enumeration attacks
    # Only send email if user actually exists
    if user is not None:
        user = await lock_user(session, user.id)
    if user is not None and user.is_active and user.email == email:
        password_reset_token = issue_account_action(
            session,
            user,
            purpose="password_reset",
            expires_delta=timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS),
        )
        await session.commit()
        email_data = await run_in_threadpool(
            generate_reset_password_email,
            email_to=user.email,
            email=email,
            token=password_reset_token,
        )
        await run_in_threadpool(
            send_email,
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return Message(
        message="If that email is registered, we sent a password recovery link"
    )


@router.post("/reset-password/")
async def reset_password(session: SessionDep, body: NewPassword) -> Message:
    """
    Reset password
    """
    if (
        await find_account_action_user_id(session, body.token, purpose="password_reset")
        is None
    ):
        raise HTTPException(status_code=400, detail="Invalid token")
    new_hash = await run_in_threadpool(security.get_password_hash, body.new_password)
    user = await consume_account_action(session, body.token, purpose="password_reset")
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid token")
    await crud.set_password(session, user, new_hash)
    await session.commit()
    return Message(message="Password updated successfully")


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(get_current_active_superuser)],
    response_class=HTMLResponse,
)
async def recover_password_html_content(email: str, session: SessionDep) -> Any:
    """
    HTML Content for Password Recovery
    """
    user = await crud.get_user_by_email(session=session, email=email)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )
    email_data = await run_in_threadpool(
        generate_reset_password_email,
        email_to=user.email,
        email=email,
        token="preview-only",
    )

    return HTMLResponse(
        content=email_data.html_content, headers={"Subject": email_data.subject}
    )
