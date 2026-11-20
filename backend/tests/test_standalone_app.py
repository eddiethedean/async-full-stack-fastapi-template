import runpy
from pathlib import Path
from shutil import copyfile

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.config import settings


@pytest.fixture
def isolated_app_entry_point(tmp_path: Path) -> Path:
    # Give each application import its own frontend directory.
    entry_point = tmp_path / "main.py"
    copyfile(Path(main.__file__), entry_point)
    return entry_point


@pytest.mark.anyio
async def test_unknown_api_routes_without_a_frontend_bundle(
    isolated_app_entry_point: Path,
) -> None:
    app = runpy.run_path(str(isolated_app_entry_point))["app"]

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
    isolated_app_entry_point: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FASTAPI_ENV")

    with pytest.raises(RuntimeError, match="Frontend directory .* does not exist"):
        runpy.run_path(str(isolated_app_entry_point))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("api_prefix", "root_path"), [("/api/v1", ""), ("/custom/api", "/proxy")]
)
async def test_frontend_fallback_preserves_api_namespace(
    isolated_app_entry_point: Path,
    api_prefix: str,
    root_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_dir = isolated_app_entry_point.parent / "frontend"
    frontend_dir.mkdir()
    index = "<!doctype html><title>Frontend fixture</title>"
    (frontend_dir / "index.html").write_text(index)
    asset = frontend_dir / api_prefix.lstrip("/") / "catalog.json"
    asset.parent.mkdir(parents=True)
    asset.write_text("{}")
    (frontend_dir / "catalog.json").write_text("{}")
    monkeypatch.setattr(settings, "API_V1_STR", api_prefix)
    app = runpy.run_path(str(isolated_app_entry_point))["app"]

    async with AsyncClient(
        transport=ASGITransport(app=app, root_path=root_path),
        base_url=f"http://testserver{root_path}",
        headers={"Accept": "text/html"},
    ) as client:
        for path in (api_prefix, f"{api_prefix}/", f"{api_prefix}/does-not-exist"):
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json() == {"detail": "Not Found"}

        assert (await client.head(f"{api_prefix}/does-not-exist")).status_code == 404
        for method in ("GET", "HEAD", "POST", "PATCH"):
            response = await client.request(method, f"{api_prefix}/catalog.json")
            assert response.status_code == 404, method

        health_path = f"{api_prefix}/utils/health-check/"
        assert (await client.get(health_path)).json() is True
        assert (await client.get(health_path.rstrip("/"))).status_code == 307
        assert (await client.post(health_path)).status_code == 405

        for path in ("/login", f"{api_prefix}-frontend"):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.text == index

        assert (await client.get("/catalog.json")).json() == {}
        assert (await client.post("/catalog.json")).status_code == 405
