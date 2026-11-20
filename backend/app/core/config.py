import warnings
from typing import Literal, Self
from urllib.parse import quote, unquote, urlsplit

from cryptography.fernet import Fernet
from pydantic import (
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.database_url import require_test_database


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None
    ENABLE_TEST_ROUTES: bool = False
    AUTH_COOKIE_SECURE: bool = True
    AUTH_TRUSTED_ORIGINS: list[str] = Field(default_factory=list)
    BROWSER_SESSION_EXPIRE_MINUTES: int = Field(default=60 * 24 * 8, gt=0)

    @property
    def auth_origins(self) -> list[str]:
        return list(
            dict.fromkeys(
                origin.rstrip("/")
                for origin in [self.FRONTEND_HOST, *self.AUTH_TRUSTED_ORIGINS]
            )
        )

    @model_validator(mode="after")
    def _validate_browser_auth(self) -> Self:
        if not self.AUTH_COOKIE_SECURE and self.FASTAPI_ENV != "development":
            raise ValueError("AUTH_COOKIE_SECURE can be disabled only in development")
        for origin in self.auth_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Browser auth requires explicit HTTP or HTTPS origins")
        return self

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    COMPOSE_POSTGRES_PASSWORD: SecretStr | None = Field(
        default=None, exclude=True, repr=False
    )
    DATABASE_URL: PostgresDsn
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=2, ge=0, le=100)
    DB_POOL_TIMEOUT_SECONDS: float = Field(default=5, gt=0, le=60)
    DB_READINESS_TIMEOUT_SECONDS: float = Field(default=1, gt=0, le=10)
    MAX_REQUEST_BODY_BYTES: int = Field(default=262_144, ge=1024)

    @model_validator(mode="before")
    @classmethod
    def _build_compose_database_url(cls, data: object) -> object:
        if not isinstance(data, dict) or "DATABASE_URL" in data:
            return data
        password = data.get("COMPOSE_POSTGRES_PASSWORD")
        if isinstance(password, SecretStr):
            password = password.get_secret_value()
        if isinstance(password, str) and password:
            return {
                **data,
                "DATABASE_URL": PostgresDsn.build(
                    scheme="postgresql+psycopg",
                    username="postgres",
                    password=quote(password, safe=""),
                    host="db",
                    port=5432,
                    path="app",
                ),
            }
        return data

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_TIMEOUT_SECONDS: int = 15
    EMAIL_OUTBOX_KEY_VERSION: str = "v1"
    EMAIL_OUTBOX_KEYS: dict[str, SecretStr] = {}
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    @model_validator(mode="after")
    def _validate_email_outbox(self) -> Self:
        if not 1 <= self.SMTP_TIMEOUT_SECONDS <= 30:
            raise ValueError("SMTP_TIMEOUT_SECONDS must be between 1 and 30")
        if self.emails_enabled and (
            self.EMAIL_OUTBOX_KEYS or self.FASTAPI_ENV != "development"
        ):
            if self.EMAIL_OUTBOX_KEY_VERSION not in self.EMAIL_OUTBOX_KEYS:
                raise ValueError("Configure the active EMAIL_OUTBOX_KEYS entry")
        for version, key in self.EMAIL_OUTBOX_KEYS.items():
            if not version or len(version) > 32 or version == "development":
                raise ValueError("Invalid email outbox key version")
            Fernet(key.get_secret_value())
        return self

    EMAIL_INVITE_TOKEN_EXPIRE_HOURS: int = 48
    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    RATE_LIMIT_WINDOW_SECONDS: int = Field(default=300, ge=1, le=86400)
    RATE_LIMIT_LOGIN_ACCOUNT: int = Field(default=30, ge=1, le=100000)
    RATE_LIMIT_LOGIN_SOURCE: int = Field(default=120, ge=1, le=100000)
    RATE_LIMIT_SIGNUP_ACCOUNT: int = Field(default=5, ge=1, le=100000)
    RATE_LIMIT_SIGNUP_SOURCE: int = Field(default=30, ge=1, le=100000)
    RATE_LIMIT_RECOVERY_ACCOUNT: int = Field(default=5, ge=1, le=100000)
    RATE_LIMIT_RECOVERY_SOURCE: int = Field(default=30, ge=1, le=100000)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        if self.ENABLE_TEST_ROUTES:
            if self.FASTAPI_ENV != "development":
                raise ValueError("Test routes require the development environment.")
            require_test_database(self.DATABASE_URL)
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        # Psycopg's SQLAlchemy dialect applies query options over the URL fields.
        query_password = make_url(str(self.DATABASE_URL)).query.get("password")
        if isinstance(query_password, str):
            self._check_default_secret("DATABASE_URL password", query_password)
        else:
            for host in self.DATABASE_URL.hosts():
                if (password := host["password"]) is not None:
                    self._check_default_secret(
                        "DATABASE_URL password", unquote(password)
                    )
            for password in query_password or ():
                self._check_default_secret("DATABASE_URL password", password)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
