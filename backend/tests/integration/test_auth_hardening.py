import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_logout_invalidates_access_and_refresh_tokens() -> None:
    tenant_id, _, email = await seed_tenant_user("auth-logout")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            response = await client.post("/api/v1/auth/logout", headers=headers)
            assert response.status_code == 204, response.text

            me = await client.get("/api/v1/auth/me", headers=headers)
            assert me.status_code == 401, me.text
            refreshed = await client.post("/api/v1/auth/refresh")
            assert refreshed.status_code == 401, refreshed.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_refresh_token_replay_is_rejected() -> None:
    tenant_id, _, email = await seed_tenant_user("auth-replay")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, email)
            old_cookie = client.cookies.get("refresh_token")
            assert old_cookie

            first = await client.post("/api/v1/auth/refresh")
            assert first.status_code == 200, first.text
            new_cookie = client.cookies.get("refresh_token")
            assert new_cookie and new_cookie != old_cookie

            # 重放已轮换的旧 refresh：必须拒绝（宽限窗口内不吊销新会话）。
            client.cookies.set("refresh_token", old_cookie)
            replay = await client.post("/api/v1/auth/refresh")
            assert replay.status_code == 401, replay.text

            client.cookies.set("refresh_token", new_cookie)
            again = await client.post("/api/v1/auth/refresh")
            assert again.status_code == 200, again.text
    finally:
        await cleanup_tenants([tenant_id])
