import re

from pydantic import PostgresDsn
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def postgres_database_name(url: PostgresDsn) -> str:
    try:
        parsed = make_url(str(url))
    except ArgumentError:
        raise ValueError("Invalid PostgreSQL connection URL.") from None

    # The psycopg dialect applies URL query options after translating the path.
    name = parsed.query.get("dbname", parsed.database)
    if not isinstance(name, str) or not name:
        raise ValueError("The PostgreSQL URL must specify one database name.")
    return name


def require_test_database(url: PostgresDsn) -> str:
    name = postgres_database_name(url)
    if not re.fullmatch(r"asf_test_[a-z0-9_]+", name):
        raise ValueError("The effective test database name must start with asf_test_.")
    return name
