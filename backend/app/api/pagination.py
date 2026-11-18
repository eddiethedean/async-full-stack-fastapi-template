from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    skip: int = Field(default=0, ge=0, le=2**63 - 1)
    limit: int = Field(default=100, ge=1, le=100)


PaginationDep = Annotated[PaginationParams, Query()]
