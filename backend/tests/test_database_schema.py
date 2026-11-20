from uuid import uuid4

import pytest
from psycopg.errors import NotNullViolation
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, insert, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Item, User
from tests.utils.item import create_random_item
from tests.utils.user import create_random_user


@pytest.mark.anyio
async def test_initial_schema_requires_item_owner(db: AsyncSession) -> None:
    statement = insert(Item).values(id=uuid4(), title="Ownerless item")

    with pytest.raises(IntegrityError) as error:
        await db.exec(statement)

    assert isinstance(error.value.orig, NotNullViolation)
    assert error.value.orig.diag.column_name == "owner_id"


@pytest.mark.anyio
@pytest.mark.parametrize("model", [User, Item], ids=["user", "item"])
async def test_initial_schema_requires_creation_timestamp(
    db: AsyncSession, model: type[User] | type[Item]
) -> None:
    record = (
        await create_random_user(db) if model is User else await create_random_item(db)
    )
    statement = update(model).where(col(model.id) == record.id).values(created_at=None)

    with pytest.raises(IntegrityError) as error:
        await db.exec(statement)

    assert isinstance(error.value.orig, NotNullViolation)
    assert error.value.orig.diag.column_name == "created_at"
