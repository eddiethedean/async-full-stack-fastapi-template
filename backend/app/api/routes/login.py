import hashlib
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import col, select
from starlette.concurrency import run_in_threadpool

from app import crud
from app.api.deps import SessionDep
from app.core import security
from app.core.config import settings
from app.core.rate_limits import enforce_rate_limit
from app.core.sessions import (
    advance_auth_version,
    consume_account_action,
    find_account_action_user_id,
    issue_account_action,
)
from app.email_outbox import enqueue_email
from app.models import (
    AuthSession,
    Message,
    NewPassword,
    RecoveryRequest,
    Token,
    User,
    get_datetime_utc,
)

router = APIRouter(tags=["login"])


@router.post("/login/access-token")
async def login_access_token(
    request: Request,
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    await enforce_rate_limit(request, action="login", email=form_data.username)
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


@router.post("/password-recovery")
async def recover_password_by_email(
    body: RecoveryRequest, session: SessionDep, request: Request
) -> Message:
    await enforce_rate_limit(request, action="recovery", email=body.email)
    if settings.emails_enabled:
        account_email = security.normalize_account_email(body.email)
        user = (
            await session.exec(
                select(User)
                .where(col(User.email) == account_email, col(User.is_active).is_(True))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).first()
        if user is not None:
            expires_delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
            token = issue_account_action(
                session, user, purpose="password_reset", expires_delta=expires_delta
            )
            await enqueue_email(
                session,
                kind="password_reset",
                recipient=user.email,
                deduplication_key=f"reset:{hashlib.sha256(token.encode()).hexdigest()}",
                user_id=user.id,
                token=token,
                expires_delta=expires_delta,
            )
            await session.commit()
    return Message(
        message="If that email is registered, we sent a password recovery link"
    )


@router.post("/invitations/accept")
async def accept_invitation(body: NewPassword, session: SessionDep) -> Message:
    if await find_account_action_user_id(session, body.token, purpose="invite") is None:
        raise HTTPException(status_code=400, detail="Invalid invitation")
    # End the preflight transaction before hashing.
    await session.rollback()
    password_hash = await run_in_threadpool(
        security.get_password_hash, body.new_password
    )
    user = await consume_account_action(
        session, body.token, purpose="invite", require_active=False
    )
    if user is None or not user.invitation_pending:
        raise HTTPException(status_code=400, detail="Invalid invitation")
    user.hashed_password = password_hash
    user.invitation_pending = False
    user.is_active = True
    await advance_auth_version(session, user)
    await session.commit()
    return Message(message="Your account is ready. Sign in to continue.")


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
