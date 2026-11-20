import logging
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from smtplib import SMTP, SMTP_SSL, SMTPRecipientsRefused
from ssl import create_default_context
from typing import Any

from jinja2 import Environment, select_autoescape

from app.core.config import settings

logging.basicConfig(level=logging.INFO)


@dataclass
class EmailData:
    html_content: str
    subject: str


def render_email_template(*, template_name: str, context: dict[str, Any]) -> str:
    template_str = (
        Path(__file__).parent / "email-templates" / template_name
    ).read_text()
    template = Environment(
        autoescape=select_autoescape(default_for_string=True)
    ).from_string(template_str)
    html_content = template.render(context)
    return html_content


def send_email(
    *,
    email_to: str,
    subject: str = "",
    html_content: str = "",
    message_id: str | None = None,
) -> None:
    if not settings.SMTP_HOST or not settings.EMAILS_FROM_EMAIL:
        raise ValueError("Email is disabled")
    message = EmailMessage()
    message["Subject"] = subject
    sender_local_part, sender_domain = settings.EMAILS_FROM_EMAIL.rsplit("@", 1)
    message["From"] = Address(
        display_name=settings.EMAILS_FROM_NAME or "",
        username=sender_local_part,
        domain=sender_domain,
    )
    message["To"] = email_to
    if message_id:
        message["Message-ID"] = f"<{message_id}>"
    message.set_content(html_content, subtype="html")
    context = create_default_context()
    smtp: SMTP
    if settings.SMTP_SSL:
        smtp = SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
            context=context,
        )
    else:
        smtp = SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
        )
    with smtp:
        if settings.SMTP_TLS and not settings.SMTP_SSL:
            smtp.starttls(context=context)
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        refused = smtp.send_message(message)
        if refused:
            raise SMTPRecipientsRefused(refused)


def generate_test_email(email_to: str) -> EmailData:
    project_name = settings.PROJECT_NAME
    subject = f"{project_name} - Test email"
    html_content = render_email_template(
        template_name="test_email.html",
        context={"project_name": settings.PROJECT_NAME, "email": email_to},
    )
    return EmailData(html_content=html_content, subject=subject)


def generate_reset_password_email(email_to: str, email: str, token: str) -> EmailData:
    project_name = settings.PROJECT_NAME
    subject = f"{project_name} - Password recovery for user {email}"
    link = f"{settings.FRONTEND_HOST}/reset-password?token={token}"
    html_content = render_email_template(
        template_name="reset_password.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "username": email,
            "email": email_to,
            "valid_hours": settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS,
            "link": link,
        },
    )
    return EmailData(html_content=html_content, subject=subject)


def generate_new_account_email(email_to: str) -> EmailData:
    subject = f"{settings.PROJECT_NAME} - Your account is ready"
    html_content = render_email_template(
        template_name="new_account.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "link": f"{settings.FRONTEND_HOST.rstrip('/')}/login",
            "email": email_to,
        },
    )
    return EmailData(html_content=html_content, subject=subject)


def generate_invitation_email(email_to: str, token: str) -> EmailData:
    subject = f"{settings.PROJECT_NAME} - Invitation"
    link = (
        f"{settings.FRONTEND_HOST.rstrip('/')}/reset-password?invite=true&token={token}"
    )
    html_content = render_email_template(
        template_name="invite.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "email": email_to,
            "link": link,
        },
    )
    return EmailData(html_content=html_content, subject=subject)
