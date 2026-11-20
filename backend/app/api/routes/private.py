from fastapi import APIRouter
from sqlmodel import Field

from app import crud
from app.api.deps import SessionDep
from app.models import User, UserCreate, UserPublic, UserRegister

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

    user_create = UserCreate.model_validate(user_in)
    return await crud.create_user(session=session, user_create=user_create)
