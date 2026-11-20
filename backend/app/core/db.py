from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.core.config import settings
from app.models import UserCreate

engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
    hide_parameters=True,
)


def create_session() -> AsyncSession:
    # Keep model attributes available for response serialization after a commit.
    return AsyncSession(engine, expire_on_commit=False)


# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


async def init_db(session: AsyncSession) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # async with engine.begin() as connection:
    #     await connection.run_sync(SQLModel.metadata.create_all)

    email = settings.FIRST_SUPERUSER
    user = await crud.get_user_by_email(session=session, email=email)
    if user is not None:
        if not (user.is_active and user.is_superuser):
            raise RuntimeError(
                "FIRST_SUPERUSER already exists and must be an active superuser."
            )
        return

    user_in = UserCreate(
        email=email,
        password=settings.FIRST_SUPERUSER_PASSWORD,
        is_superuser=True,
    )
    try:
        await crud.create_user(session=session, user_create=user_in)
    except crud.DuplicateEmailError:
        # create_user rolls back the failed insert before reporting the conflict.
        user = await crud.get_user_by_email(session=session, email=email)
        if user is None or not (user.is_active and user.is_superuser):
            raise
