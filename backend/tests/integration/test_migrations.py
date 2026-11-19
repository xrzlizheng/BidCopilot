import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


@pytest.mark.integration
async def test_identity_tables_exist() -> None:
    engine = create_async_engine(get_settings().DATABASE_URL)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    await engine.dispose()

    assert {"tenants", "users", "roles", "user_roles", "audit_events"} <= tables
