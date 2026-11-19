from anyio import fail_after
from fastapi import APIRouter, Depends, HTTPException
from pydantic.networks import EmailStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_active_superuser
from app.core.config import settings
from app.core.db import engine
from app.models import Message
from app.utils import generate_test_email, send_email

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_active_superuser)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
async def health_check() -> bool:
    return True


@router.get("/readiness/")
async def readiness() -> Message:
    try:
        with fail_after(settings.DB_READINESS_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except TimeoutError, SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database not ready")
    return Message(message="Ready")
