import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.integration
async def test_readyz_reports_database_and_redis() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }
