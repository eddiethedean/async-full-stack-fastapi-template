import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings

UPGRADE = """
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

directory, worker = Path(sys.argv[1]), int(sys.argv[2])
ready, peer_ready = directory / str(worker), directory / str(1 - worker)


@event.listens_for(Engine, "before_cursor_execute")
def coordinate_upgrade(connection, cursor, statement, parameters, context, executemany):
    if "pg_advisory_xact_lock" in statement:
        ready.touch()
    elif statement.lstrip().startswith("CREATE TABLE alembic_version"):
        # Without serialization, both commands must reach CREATE TABLE before
        # either executes it. With serialization, the peer can reach the lock.
        ready.touch()
        deadline = time.monotonic() + 15
        while not peer_ready.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Peer migration did not reach the initialization boundary")
            time.sleep(0.01)


command.upgrade(Config("alembic.ini"), "head")
"""


def test_concurrent_upgrades_initialize_an_empty_schema(tmp_path: Path) -> None:
    database_url = make_url(str(settings.DATABASE_URL))
    schema = f"migration_test_{uuid4().hex}"
    schema_url = database_url.update_query_dict({"options": f"-c search_path={schema}"})
    engine = create_engine(database_url)

    def upgrade(worker: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", UPGRADE, str(tmp_path), str(worker)],
            env={
                **os.environ,
                "DATABASE_URL": schema_url.render_as_string(hide_password=False),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(upgrade, (0, 1)))
        assert all(result.returncode == 0 for result in results), "\n".join(
            result.stderr for result in results
        )
        assert inspect(engine).has_table("user", schema=schema)
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        engine.dispose()
