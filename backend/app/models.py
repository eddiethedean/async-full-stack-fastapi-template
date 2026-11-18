import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import MISSING, EmailStr, StrictInt
from sqlalchemy import Index, text
from sqlmodel import Field, Relationship, SQLModel, col


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | MISSING = Field(default=MISSING, max_length=255)
    is_active: bool | MISSING = MISSING
    is_superuser: bool | MISSING = MISSING
    full_name: str | None | MISSING = Field(default=MISSING, max_length=255)
    password: str | MISSING = Field(default=MISSING, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None | MISSING = Field(default=MISSING, max_length=255)
    email: EmailStr | MISSING = Field(default=MISSING, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    auth_version: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


class AuthSession(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    kind: str = Field(max_length=16)
    auth_version: int
    cookie_digest: str | None = Field(default=None, max_length=64, unique=True)
    created_at: datetime = Field(default_factory=get_datetime_utc)
    expires_at: datetime = Field(index=True)
    revoked_at: datetime | None = None


class AccountActionToken(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    purpose: str = Field(max_length=32)
    digest: str = Field(max_length=64, unique=True)
    auth_version: int
    created_at: datetime = Field(default_factory=get_datetime_utc)
    expires_at: datetime = Field(index=True)
    consumed_at: datetime | None = None


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | MISSING = Field(default=MISSING, min_length=1, max_length=255)
    description: str | None | MISSING = Field(default=MISSING, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


Index(
    "ix_item_owner_created_at_id",
    col(Item.owner_id),
    col(Item.created_at).desc().nulls_last(),
    col(Item.id).desc(),
)


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: uuid.UUID
    sid: uuid.UUID
    exp: StrictInt
    iat: StrictInt
    purpose: Literal["access"]


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
