import pytest

from app.db.session import engine


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine_after_integration_tests():
    yield
    await engine.dispose()
