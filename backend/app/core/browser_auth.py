import hmac

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings
from app.models import AuthSession

COOKIE_NAME = "__Host-asf_session" if settings.AUTH_COOKIE_SECURE else "asf_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def set_session_cookie(
    response: Response, credential: str, auth_session: AuthSession
) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=credential,
        max_age=settings.BROWSER_SESSION_EXPIRE_MINUTES * 60,
        expires=auth_session.expires_at,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def csrf_token(auth_session: AuthSession) -> str:
    return hmac.digest(
        settings.SECRET_KEY.encode(),
        b"browser-csrf:v1:" + auth_session.id.bytes,
        "sha256",
    ).hex()


def check_origin(request: Request) -> None:
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] not in settings.auth_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed",
            headers={"Cache-Control": "no-store"},
        )


def check_csrf(request: Request, auth_session: AuthSession) -> None:
    check_origin(request)
    provided = request.headers.get(CSRF_HEADER, "").encode()
    if not hmac.compare_digest(provided, csrf_token(auth_session).encode()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
            headers={"Cache-Control": "no-store"},
        )
