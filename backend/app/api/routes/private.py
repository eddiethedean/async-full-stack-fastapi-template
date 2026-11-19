from fastapi import APIRouter
from sqlmodel import Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import SessionDep
from app.core.security import get_password_hash
from app.models import User, UserPublic, UserRegister

router = APIRouter(tags=["private"], prefix="/private")


@router.get("/test-mode")
def test_mode() -> dict[str, bool]:
    return {"test_mode": True}


class PrivateUserCreate(UserRegister):
    full_name: str = Field(max_length=255)
    is_verified: bool = False


@router.post("/users/", response_model=UserPublic)
async def create_user(user_in: PrivateUserCreate, session: SessionDep) -> User:
    """
    Create a new user.
    """

    user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=await run_in_threadpool(get_password_hash, user_in.password),
    )

    session.add(user)
    await session.commit()

    return user
