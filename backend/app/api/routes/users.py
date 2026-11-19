import secrets
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import col, delete
from starlette.concurrency import run_in_threadpool

from app import crud
from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_credentials_error,
    get_current_active_superuser,
)
from app.api.pagination import PaginationDep, fetch_page
from app.core.config import settings
from app.core.rate_limits import enforce_rate_limit
from app.core.security import get_password_hash
from app.core.sessions import issue_account_action, lock_user
from app.email_outbox import enqueue_email
from app.models import (
    Item,
    Message,
    UpdatePassword,
    User,
    UserCreate,
    UserInvite,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)

router = APIRouter(prefix="/users", tags=["users"])
email_conflict: dict[int | str, dict[str, Any]] = {
    409: {"description": "User with this email already exists"}
}


@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
async def read_users(session: SessionDep, pagination: PaginationDep) -> UsersPublic:
    """
    Retrieve users.
    """

    users, count = await fetch_page(session, User, pagination)

    users_public = [UserPublic.model_validate(user) for user in users]
    return UsersPublic(data=users_public, count=count)


@router.post(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
    responses=email_conflict,
)
async def create_user(*, session: SessionDep, user_in: UserCreate) -> Any:
    """
    Create new user.
    """
    hashed_password = await run_in_threadpool(get_password_hash, user_in.password)
    user = User.model_validate(user_in, update={"hashed_password": hashed_password})
    if settings.emails_enabled:
        await enqueue_email(
            session,
            kind="welcome",
            recipient=user.email,
            deduplication_key=f"welcome:{user.id}",
        )
    return await crud.commit_user(session, user)


@router.post(
    "/invite",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
    status_code=201,
    responses=email_conflict,
)
async def invite_user(session: SessionDep, user_in: UserInvite) -> User:
    if not settings.emails_enabled:
        raise HTTPException(status_code=503, detail="Email delivery is unavailable")

    # Pending accounts have no usable password until the recipient accepts.
    placeholder = await run_in_threadpool(get_password_hash, secrets.token_urlsafe(32))
    user = User.model_validate(
        user_in,
        update={
            "hashed_password": placeholder,
            "is_active": False,
            "invitation_pending": True,
        },
    )
    await crud.flush_user(session, user)
    expires_delta = timedelta(hours=settings.EMAIL_INVITE_TOKEN_EXPIRE_HOURS)
    token = issue_account_action(
        session, user, purpose="invite", expires_delta=expires_delta
    )
    await enqueue_email(
        session,
        kind="invite",
        recipient=user.email,
        deduplication_key=f"invite:{user.id}",
        token=token,
        expires_delta=expires_delta,
    )
    return await crud.commit_user(session, user)


@router.patch("/me", response_model=UserPublic, responses=email_conflict)
async def update_user_me(
    *, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser
) -> Any:
    """
    Update own user.
    """

    user = await crud.update_user(
        session=session,
        db_user=current_user,
        user_in=user_in,
        expected_auth_version=current_user.auth_version,
    )
    if user is None:
        raise get_credentials_error()
    return user


@router.patch("/me/password", response_model=Message)
async def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: CurrentUser
) -> Any:
    """
    Update own password.
    """
    result = await crud.change_password(
        session=session,
        user=current_user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    if result == "incorrect":
        raise HTTPException(status_code=400, detail="Incorrect password")
    if result == "unchanged":
        raise HTTPException(
            status_code=400, detail="New password cannot be the same as the current one"
        )
    return Message(message="Password updated successfully")


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    """
    Get current user.
    """
    return current_user


@router.delete("/me", response_model=Message)
async def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """
    Delete own user.
    """
    user = await lock_user(session, current_user.id)
    if user is None:
        raise get_credentials_error()
    if user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    await session.delete(user)
    await session.commit()
    return Message(message="User deleted successfully")


@router.post("/signup", response_model=UserPublic, responses=email_conflict)
async def register_user(
    session: SessionDep, user_in: UserRegister, request: Request
) -> Any:
    """
    Create new user without the need to be logged in.
    """
    await enforce_rate_limit(request, action="signup", email=user_in.email)
    user_create = UserCreate.model_validate(user_in)
    return await crud.create_user(session=session, user_create=user_create)


@router.get("/{user_id}", response_model=UserPublic)
async def read_user_by_id(
    user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Any:
    """
    Get a specific user by id.
    """
    user = await session.get(User, user_id)
    if user == current_user:
        return user
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="The user doesn't have enough privileges",
        )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch(
    "/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
    responses={409: {"description": "Email conflict or conflicting superuser change"}},
)
async def update_user(
    *,
    session: SessionDep,
    user_id: uuid.UUID,
    user_in: UserUpdate,
) -> Any:
    """
    Update a user.
    """

    db_user = await session.get(User, user_id)
    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    db_user = await crud.update_user(session=session, db_user=db_user, user_in=user_in)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user


@router.delete(
    "/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    responses={409: {"description": "Conflicting superuser change"}},
)
async def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Message:
    """
    Delete a user.
    """
    await crud.lock_active_superuser_removal(session)
    user = await lock_user(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user == current_user:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    await crud.require_other_active_superuser(session, user)
    statement = delete(Item).where(col(Item.owner_id) == user_id)
    await session.exec(statement)
    await session.delete(user)
    await session.commit()
    return Message(message="User deleted successfully")
