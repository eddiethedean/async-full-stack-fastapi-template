import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.pagination import PaginationParams
from app.api.routes.items import read_items
from app.core.config import settings
from app.core.db import create_session
from app.crud import create_user
from app.models import Item, UserCreate
from tests.utils.item import create_random_item
from tests.utils.user import create_random_user, user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

pytestmark = pytest.mark.anyio


async def test_create_item(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"title": "Foo", "description": "Fighters"}
    response = await client.post(
        f"{settings.API_V1_STR}/items/",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["title"] == data["title"]
    assert content["description"] == data["description"]
    assert "id" in content
    assert "owner_id" in content


async def test_read_item(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    response = await client.get(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["title"] == item.title
    assert content["description"] == item.description
    assert content["id"] == str(item.id)
    assert content["owner_id"] == str(item.owner_id)


async def test_read_item_not_found(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{settings.API_V1_STR}/items/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404
    content = response.json()
    assert content["detail"] == "Item not found"


async def test_read_item_not_enough_permissions(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    response = await client.get(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 403
    content = response.json()
    assert content["detail"] == "Not enough permissions"


async def test_read_items(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    await create_random_item(db)
    await create_random_item(db)
    response = await client.get(
        f"{settings.API_V1_STR}/items/",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert len(content["data"]) >= 2


async def test_read_items_as_normal_user(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    await create_random_item(db)
    url = f"{settings.API_V1_STR}/items/"
    created = await client.post(
        url, headers=normal_user_token_headers, json={"title": "My item"}
    )
    assert created.status_code == 200

    response = await client.get(url, headers=normal_user_token_headers)
    assert response.status_code == 200
    assert response.json() == {"count": 1, "data": [created.json()]}


@pytest.mark.parametrize(
    "params", [{"skip": -1}, {"skip": 2**63}, {"limit": 0}, {"limit": 101}]
)
async def test_item_pagination_bounds(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    params: dict[str, int],
) -> None:
    response = await client.get(
        f"{settings.API_V1_STR}/items/",
        headers=normal_user_token_headers,
        params=params,
    )
    assert response.status_code == 422


async def test_item_pages_have_stable_order_and_filtered_total(
    client: AsyncClient, db: AsyncSession
) -> None:
    email, password = random_email(), random_lower_string()
    user = await create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    other_user = await create_random_user(db)
    same_time = datetime.now(UTC)
    ids = sorted([uuid.uuid4(), uuid.uuid4()], reverse=True)
    legacy_item = Item(title="Last", owner_id=user.id)
    db.add_all(
        [
            Item(id=ids[0], title="First", owner_id=user.id, created_at=same_time),
            Item(id=ids[1], title="Second", owner_id=user.id, created_at=same_time),
            legacy_item,
            Item(title="Another user", owner_id=other_user.id, created_at=same_time),
        ]
    )
    await db.flush()
    legacy_item.created_at = None
    await db.commit()
    headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    url = f"{settings.API_V1_STR}/items/"
    results = []
    for skip in (0, 2, 4):
        response = await client.get(
            url, headers=headers, params={"skip": skip, "limit": 2}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["count"] == 3
        results.append([row["id"] for row in result["data"]])

    assert results == [[str(item_id) for item_id in ids], [str(legacy_item.id)], []]


async def test_item_page_and_total_share_a_snapshot(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_random_user(db)
    first = Item(title="First item", owner_id=user.id)
    second = Item(title="Second item", owner_id=user.id)
    db.add_all([first, second])
    await db.commit()

    execute = db.exec
    deleted = False

    async def delete_after_first_query(statement: Any) -> Any:
        nonlocal deleted
        result = await execute(statement)
        if not deleted:
            async with create_session() as writer:
                item = await writer.get(Item, second.id)
                assert item is not None
                await writer.delete(item)
                await writer.commit()
            deleted = True
        return result

    monkeypatch.setattr(db, "exec", delete_after_first_query)

    page = await read_items(db, user, PaginationParams(skip=1, limit=1))

    assert deleted
    assert page.count == 2
    assert len(page.data) == 1


async def test_update_item(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    data = {"title": "Updated title", "description": "Updated description"}
    response = await client.put(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["title"] == data["title"]
    assert content["description"] == data["description"]
    assert content["id"] == str(item.id)
    assert content["owner_id"] == str(item.owner_id)


async def test_update_item_not_found(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"title": "Updated title", "description": "Updated description"}
    response = await client.put(
        f"{settings.API_V1_STR}/items/{uuid.uuid4()}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 404
    content = response.json()
    assert content["detail"] == "Item not found"


async def test_update_item_deleted_after_read(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email, password = random_email(), random_lower_string()
    user = await create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    item = Item(title="Original title", owner_id=user.id)
    db.add(item)
    await db.commit()
    headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    get = AsyncSession.get

    async def delete_after_read(
        session: AsyncSession, entity: type[Any], ident: Any, **kwargs: Any
    ) -> Any:
        row = await get(session, entity, ident, **kwargs)
        if entity is Item and ident == item.id and row is not None:
            async with create_session() as writer, writer.begin():
                target = await get(writer, Item, item.id)
                assert target is not None
                await writer.delete(target)
        return row

    monkeypatch.setattr(AsyncSession, "get", delete_after_read)
    response = await client.put(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=headers,
        json={"title": "Updated title"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Item not found"}
    async with create_session() as reader:
        assert await get(reader, Item, item.id) is None


async def test_update_item_distinguishes_omission_from_null(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    headers = superuser_token_headers
    created = await client.post(
        f"{settings.API_V1_STR}/items/",
        headers=headers,
        json={"title": "Original title", "description": "Original description"},
    )
    assert created.status_code == 200
    url = f"{settings.API_V1_STR}/items/{created.json()['id']}"

    unchanged = await client.put(url, headers=headers, json={})
    assert unchanged.json() == created.json()

    invalid = await client.put(url, headers=headers, json={"title": None})
    assert invalid.status_code == 422

    cleared = await client.put(url, headers=headers, json={"description": None})
    assert cleared.status_code == 200
    assert cleared.json()["title"] == "Original title"
    assert cleared.json()["description"] is None


async def test_update_item_not_enough_permissions(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    data = {"title": "Updated title", "description": "Updated description"}
    response = await client.put(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=normal_user_token_headers,
        json=data,
    )
    assert response.status_code == 403
    content = response.json()
    assert content["detail"] == "Not enough permissions"


async def test_delete_item(
    client: AsyncClient, superuser_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    response = await client.delete(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["message"] == "Item deleted successfully"


async def test_delete_item_not_found(
    client: AsyncClient, superuser_token_headers: dict[str, str]
) -> None:
    response = await client.delete(
        f"{settings.API_V1_STR}/items/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404
    content = response.json()
    assert content["detail"] == "Item not found"


async def test_delete_item_not_enough_permissions(
    client: AsyncClient, normal_user_token_headers: dict[str, str], db: AsyncSession
) -> None:
    item = await create_random_item(db)
    response = await client.delete(
        f"{settings.API_V1_STR}/items/{item.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 403
    content = response.json()
    assert content["detail"] == "Not enough permissions"
