import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from pydantic.networks import validate_email

from app.core.config import settings
from app.models import AuthSession, TokenPayload

password_hash = PasswordHash(
    (
        Argon2Hasher(),
        BcryptHasher(),
    )
)

# Spend Argon2 verification work on known-invalid login attempts.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"

ALGORITHM = "HS256"
TOKEN_ISSUER = "async-full-stack-fastapi"
TOKEN_AUDIENCE = "api"


def normalize_account_email(email: str) -> str:
    try:
        return validate_email(email)[1]
    except ValueError:
        return email.strip()


def create_access_token(auth_session: AuthSession) -> str:
    payload = {
        "exp": auth_session.expires_at,
        "iat": auth_session.created_at,
        "sub": str(auth_session.user_id),
        "sid": str(auth_session.id),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "purpose": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> TokenPayload:
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[ALGORITHM],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={"require": ["exp", "iat", "sub", "sid", "iss", "aud", "purpose"]},
    )
    return TokenPayload.model_validate(payload)


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    if (
        BcryptHasher.identify(hashed_password)
        and len(plain_password.encode("utf-8")) > 72
    ):
        # A legacy hash cannot verify the suffix. Recover through a password reset.
        password_hash.verify(plain_password, DUMMY_HASH)
        return False, None
    return password_hash.verify_and_update(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)
