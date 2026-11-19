import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.system.models import ModelSecret
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_personal_model_key_is_write_only_and_isolated() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("mkey-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "mkey-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)

            saved = await client.put(
                "/api/v1/system/model-configs/personal/main",
                json={
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "deepseek-flash",
                    "api_key": "sk-test-secret-1234",
                    "enabled": True,
                },
                headers=engineer,
            )
            assert saved.status_code == 200, saved.text
            body = saved.json()
            assert body["key_last4"] == "1234"
            assert "api_key" not in body
            assert "sk-test-secret-1234" not in saved.text

            personal = await client.get(
                "/api/v1/system/model-configs/personal", headers=engineer
            )
            assert personal.status_code == 200
            main = next(row for row in personal.json() if row["purpose"] == "main")
            assert main["key_last4"] == "1234"
            assert main["configured"] is True
            assert "api_key" not in main

            admin_personal = await client.get(
                "/api/v1/system/model-configs/personal", headers=admin
            )
            assert admin_personal.status_code == 200
            assert all(row["configured"] is False for row in admin_personal.json())

            tenant_attempt = await client.patch(
                "/api/v1/system/model-configs/main",
                json={
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "deepseek-flash",
                    "enabled": True,
                },
                headers=engineer,
            )
            assert tenant_attempt.status_code == 403

            enterprise = await client.patch(
                "/api/v1/system/model-configs/main",
                json={
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "deepseek-flash",
                    "api_key": "sk-enterprise-9876",
                    "enabled": True,
                },
                headers=admin,
            )
            assert enterprise.status_code == 200, enterprise.text
            assert enterprise.json()["key_last4"] == "9876"
            assert "sk-enterprise-9876" not in enterprise.text

            status = await client.get("/api/v1/system/ai-status", headers=admin)
            assert status.status_code == 200
            assert status.json()["llm"]["configured"] is True
            assert status.json()["llm"]["model"] == "deepseek-flash"
            assert status.json()["llm"]["host"] == "127.0.0.1:9999"

            async with SessionLocal() as session:
                secrets = (
                    await session.execute(
                        select(ModelSecret).where(ModelSecret.tenant_id == tenant_id)
                    )
                ).scalars().all()
                assert secrets
                for secret in secrets:
                    assert b"sk-test-secret-1234" not in secret.ciphertext
                    assert b"sk-enterprise-9876" not in secret.ciphertext

            revoked = await client.delete(
                "/api/v1/system/model-configs/personal/main", headers=engineer
            )
            assert revoked.status_code == 204
            after_revoke = await client.get(
                "/api/v1/system/model-configs/personal", headers=engineer
            )
            main_after = next(
                row for row in after_revoke.json() if row["purpose"] == "main"
            )
            assert main_after["configured"] is False
    finally:
        await cleanup_tenants([tenant_id])
