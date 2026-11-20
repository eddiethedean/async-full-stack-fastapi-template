from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import aliased
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class PaginationParams(BaseModel):
    skip: int = Field(default=0, ge=0, le=2**63 - 1)
    limit: int = Field(default=100, ge=1, le=100)


PaginationDep = Annotated[PaginationParams, Query()]


async def fetch_page[Model: SQLModel](
    session: AsyncSession,
    model: type[Model],
    pagination: PaginationParams,
    *filters: ColumnElement[bool],
) -> tuple[list[Model], int]:
    matching = select(model).where(*filters)
    columns = matching.selected_columns
    page = (
        matching.order_by(columns.created_at.desc(), columns.id.desc())
        .offset(pagination.skip)
        .limit(pagination.limit)
        .subquery()
    )
    total = (
        select(func.count().label("row_count"))
        .select_from(matching.subquery())
        .subquery()
    )
    page_model = aliased(model, page)

    # The count supplies a row even when the requested page is empty.
    statement = (
        select(page_model, total.c.row_count)
        .select_from(total)
        .outerjoin(page, true())
        .order_by(page.c.created_at.desc(), page.c.id.desc())
    )
    rows = (await session.exec(statement)).all()
    return [row for row, _ in rows if row is not None], int(rows[0][1])
