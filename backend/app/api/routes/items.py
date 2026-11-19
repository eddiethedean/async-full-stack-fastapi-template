import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, update

from app import crud
from app.api.deps import CurrentUser, SessionDep
from app.api.pagination import PaginationDep, fetch_page
from app.models import Item, ItemCreate, ItemPublic, ItemsPublic, ItemUpdate, Message

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=ItemsPublic)
async def read_items(
    session: SessionDep, current_user: CurrentUser, pagination: PaginationDep
) -> ItemsPublic:
    """
    Retrieve items.
    """

    filters = (
        () if current_user.is_superuser else (col(Item.owner_id) == current_user.id,)
    )
    items, count = await fetch_page(session, Item, pagination, *filters)
    items_public = [ItemPublic.model_validate(item) for item in items]
    return ItemsPublic(data=items_public, count=count)


@router.get("/{id}", response_model=ItemPublic)
async def read_item(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """
    Get item by ID.
    """
    item = await session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
async def create_item(
    *, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate
) -> Item:
    """
    Create new item.
    """
    return await crud.create_item(
        session=session, item_in=item_in, owner_id=current_user.id
    )


@router.put("/{id}", response_model=ItemPublic)
async def update_item(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    item_in: ItemUpdate,
) -> Any:
    """
    Update an item.
    """
    item = await session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = item_in.model_dump(exclude_unset=True)
    if not update_dict:
        return item

    statement = update(Item).where(col(Item.id) == id)
    if not current_user.is_superuser:
        statement = statement.where(col(Item.owner_id) == current_user.id)
    result = await session.exec(statement.values(update_dict).returning(Item))
    updated = result.scalar_one_or_none()
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return updated


@router.delete("/{id}")
async def delete_item(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an item.
    """
    item = await session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    await session.delete(item)
    await session.commit()
    return Message(message="Item deleted successfully")
