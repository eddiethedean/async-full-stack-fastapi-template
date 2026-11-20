import runpy
from pathlib import Path
from shutil import copyfile

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.config import settings


@pytest.fixture
def entry_point_without_frontend(tmp_path: Path) -> Path:
    # Load the application entry point beside a guaranteed absent bundle.
    entry_point = tmp_path / "main.py"
    copyfile(Path(main.__file__), entry_point)
    return entry_point


@pytest.mark.anyio
async def test_unknown_api_routes_without_a_frontend_bundle(
    entry_point_without_frontend: Path,
) -> None:
    app = runpy.run_path(str(entry_point_without_frontend))["app"]

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        prefix = settings.API_V1_STR
        schema = await client.get(f"{prefix}/openapi.json")
        assert schema.status_code == 200

        for method in ("GET", "POST"):
            response = await client.request(method, f"{prefix}/does-not-exist")
            assert response.status_code == 404


def test_production_requires_a_frontend_bundle(
    entry_point_without_frontend: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FASTAPI_ENV")

    with pytest.raises(RuntimeError, match="Frontend directory .* does not exist"):
        runpy.run_path(str(entry_point_without_frontend))
