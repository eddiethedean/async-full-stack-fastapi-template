import hmac
import logging
from datetime import UTC, datetime, timedelta
from ipaddress import IPv6Address, ip_address
from math import ceil
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, Request
from opentelemetry.metrics import get_meter
from sqlalchemy import tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field, SQLModel, col, delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import create_session

AuthAction = Literal["login", "signup", "recovery"]
logger = logging.getLogger(__name__)
requests = get_meter(__name__).create_counter(
    "auth.rate_limit.requests", description="Authentication rate limit decisions"
)


class RateLimitBucket(SQLModel, table=True):
    __tablename__ = "rate_limit_bucket"

    action: str = Field(primary_key=True, max_length=16)
    kind: str = Field(primary_key=True, max_length=8)
    key_hash: str = Field(primary_key=True, max_length=64)
    window: datetime = Field(primary_key=True)
    expires_at: datetime = Field(index=True)
    count: int


def rate_limit_hash(kind: str, value: str) -> str:
    return hmac.digest(
        settings.SECRET_KEY.encode(),
        f"rate-limit/v1/{kind}\x00{value}".encode(),
        "sha256",
    ).hex()


def normalize_rate_identity(email: str) -> str:
    trimmed = email.strip()
    try:
        return validate_email(trimmed, check_deliverability=False).normalized
    except EmailNotValidError:
        return trimmed


def client_address(request: Request) -> str:
    # The ASGI server accepts forwarded addresses only from configured peers.
    if request.client is None:
        return "unavailable"
    try:
        address = ip_address(request.client.host)
    except ValueError:
        return "unavailable"
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def limits_for(action: AuthAction) -> tuple[int, int]:
    match action:
        case "login":
            return settings.RATE_LIMIT_LOGIN_ACCOUNT, settings.RATE_LIMIT_LOGIN_SOURCE
        case "signup":
            return settings.RATE_LIMIT_SIGNUP_ACCOUNT, settings.RATE_LIMIT_SIGNUP_SOURCE
        case "recovery":
            return (
                settings.RATE_LIMIT_RECOVERY_ACCOUNT,
                settings.RATE_LIMIT_RECOVERY_SOURCE,
            )


async def purge_rate_limit_buckets(
    session: AsyncSession, *, batch_size: int = 100
) -> None:
    expired = (
        select(
            RateLimitBucket.action,
            RateLimitBucket.kind,
            RateLimitBucket.key_hash,
            RateLimitBucket.window,
        )
        .where(col(RateLimitBucket.expires_at) <= func.statement_timestamp())
        .order_by(col(RateLimitBucket.expires_at))
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    await session.exec(
        delete(RateLimitBucket).where(
            tuple_(
                col(RateLimitBucket.action),
                col(RateLimitBucket.kind),
                col(RateLimitBucket.key_hash),
                col(RateLimitBucket.window),
            ).in_(expired)
        )
    )


async def check_rate_limits(
    action: AuthAction, *, email: str, source: str
) -> int | None:
    seconds = settings.RATE_LIMIT_WINDOW_SECONDS
    limit_account, limit_source = limits_for(action)
    async with create_session() as session, session.begin():
        now = (await session.exec(select(func.statement_timestamp()))).one()
        start = int(now.timestamp()) // seconds * seconds
        window = datetime.fromtimestamp(start, UTC)
        expires_at = window + timedelta(seconds=seconds)
        exceeded = False
        for kind, value, limit in (
            ("account", normalize_rate_identity(email), limit_account),
            ("source", source, limit_source),
        ):
            stmt = insert(RateLimitBucket).values(
                action=action,
                kind=kind,
                key_hash=rate_limit_hash(kind, value),
                window=window,
                expires_at=expires_at,
                count=1,
            )
            upsert = stmt.on_conflict_do_update(
                index_elements=["action", "kind", "key_hash", "window"],
                set_={"count": func.least(col(RateLimitBucket.count) + 1, limit + 1)},
            ).returning(col(RateLimitBucket.count))
            count = (await session.exec(upsert)).scalar_one()
            exceeded |= count > limit
        await purge_rate_limit_buckets(session, batch_size=20)
    return max(1, ceil((expires_at - now).total_seconds())) if exceeded else None


async def enforce_rate_limit(
    request: Request, *, action: AuthAction, email: str
) -> None:
    try:
        retry_after = await check_rate_limits(
            action, email=email, source=client_address(request)
        )
    except SQLAlchemyError:
        logger.warning("auth_rate_limit datastore_unavailable action=%s", action)
        requests.add(1, {"action": action, "outcome": "unavailable"})
        raise HTTPException(status_code=503, detail="Sign-in service unavailable")

    requests.add(
        1, {"action": action, "outcome": "limited" if retry_after else "allowed"}
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
