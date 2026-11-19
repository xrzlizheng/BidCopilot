from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
