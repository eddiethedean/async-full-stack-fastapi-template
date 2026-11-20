from uuid import uuid4

import pytest
from psycopg.errors import NotNullViolation
from sqlalchemy.exc import IntegrityError
from sqlmodel import insert
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Item


@pytest.mark.anyio
async def test_initial_schema_requires_item_owner(db: AsyncSession) -> None:
    statement = insert(Item).values(id=uuid4(), title="Ownerless item")

    with pytest.raises(IntegrityError) as error:
        await db.exec(statement)

    assert isinstance(error.value.orig, NotNullViolation)
    assert error.value.orig.diag.column_name == "owner_id"
