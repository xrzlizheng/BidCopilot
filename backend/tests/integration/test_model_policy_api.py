import pytest
from httpx import ASGITransport, AsyncClient

from app.core.errors import ForbiddenError
from app.db.session import SessionLocal
from app.main import app
from app.modules.generation.deps import get_llm_client
from app.modules.identity.service import load_user_context
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_personal_model_requires_tenant_policy_and_uses_personal_key() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("mpolicy-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "mpolicy-engineer", "bid_engineer", tenant_id
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
                    "api_key": "sk-personal-1234",
                    "enabled": True,
                },
                headers=engineer,
            )
            assert saved.status_code == 200, saved.text

            policy = await client.get("/api/v1/system/model-policy", headers=admin)
            assert policy.status_code == 200
            assert policy.json()["allow_personal_key_for_enterprise_data"] is False

            async with SessionLocal() as session:
                user = await load_user_context(session, engineer_id)
                assert user is not None
                with pytest.raises(ForbiddenError) as exc:
                    await get_llm_client(
                        session=session, user=user, model_scope="personal"
                    )
                assert exc.value.code == "policy_denied"

            enabled = await client.patch(
                "/api/v1/system/model-policy",
                json={"allow_personal_key_for_enterprise_data": True},
                headers=admin,
            )
            assert enabled.status_code == 200, enabled.text
            assert enabled.json()["allow_personal_key_for_enterprise_data"] is True

            async with SessionLocal() as session:
                user = await load_user_context(session, engineer_id)
                assert user is not None
                client_llm = await get_llm_client(
                    session=session, user=user, model_scope="personal"
                )
                assert client_llm.base_url == "http://127.0.0.1:9999/v1"
                assert client_llm.model == "deepseek-flash"
                assert client_llm.api_key == "sk-personal-1234"
    finally:
        await cleanup_tenants([tenant_id])
