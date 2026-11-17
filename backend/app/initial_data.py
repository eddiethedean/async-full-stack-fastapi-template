import asyncio
import logging

from app.core.db import create_session, engine, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Creating initial data")
    try:
        async with create_session() as session:
            await init_db(session)
    finally:
        await engine.dispose()
    logger.info("Initial data created")


if __name__ == "__main__":
    # Async Psycopg requires a selector loop on Windows.
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
