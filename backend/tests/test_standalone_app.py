import runpy
from pathlib import Path
from shutil import copyfile

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.config import settings


@pytest.mark.anyio
async def test_unknown_api_routes_without_a_frontend_bundle(tmp_path: Path) -> None:
    # Load the application entry point beside a guaranteed absent bundle.
    entry_point = tmp_path / "main.py"
    copyfile(Path(main.__file__), entry_point)
    app = runpy.run_path(str(entry_point))["app"]

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
