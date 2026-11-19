import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.knowledge.models import KnowledgeBase, KnowledgePack
from app.modules.system.models import ModelConfig, ModelSecret
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_disabled_user_resources_are_orphaned_and_transferable() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("offboard-admin")
    _, owner_id, owner_email = await seed_tenant_user(
        "offboard-owner", "bid_engineer", tenant_id
    )
    _, target_id, target_email = await seed_tenant_user(
        "offboard-target", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            owner = await login(client, owner_email)

            kb = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "离职个人库", "scope": "personal"},
                headers=owner,
            )
            assert kb.status_code == 201, kb.text
            pack = await client.post(
                "/api/v1/knowledge-packs",
                json={
                    "name": "离职个人模板",
                    "category": "structure",
                    "scope": "personal",
                    "content_json": {
                        "sections": [
                            {
                                "title": "章节",
                                "purpose": "测试",
                                "guidance": "测试",
                                "required_materials": ["材料"],
                            }
                        ]
                    },
                },
                headers=owner,
            )
            assert pack.status_code == 201, pack.text
            config = await client.put(
                "/api/v1/system/model-configs/personal/main",
                json={
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "deepseek-flash",
                    "api_key": "sk-offboard-1234",
                    "enabled": True,
                },
                headers=owner,
            )
            assert config.status_code == 200, config.text

            disabled = await client.patch(
                f"/api/v1/users/{owner_id}",
                json={"status": "disabled"},
                headers=admin,
            )
            assert disabled.status_code == 200, disabled.text

            async with SessionLocal() as session:
                kb_row = await session.get(KnowledgeBase, kb.json()["id"])
                pack_row = await session.get(KnowledgePack, pack.json()["id"])
                cfg = (
                    await session.execute(
                        select(ModelConfig).where(
                            ModelConfig.owner_id == owner_id,
                            ModelConfig.scope == "personal",
                        )
                    )
                ).scalar_one()
                secret = await session.get(ModelSecret, cfg.secret_ref)
                assert kb_row.owner_status == "orphaned"
                assert pack_row.owner_status == "orphaned"
                assert cfg.enabled is False
                assert secret.revoked_at is not None

            metadata = await client.get(
                "/api/v1/compliance/personal-resources", headers=admin
            )
            kb_meta = next(row for row in metadata.json() if row["id"] == kb.json()["id"])
            assert kb_meta["owner_status"] == "orphaned"

            transferred = await client.post(
                f"/api/v1/compliance/personal-resources/knowledge_base/{kb.json()['id']}/transfer",
                json={"target_user_id": str(target_id)},
                headers=admin,
            )
            assert transferred.status_code == 200, transferred.text
            assert transferred.json()["owner_status"] == "active"

            pack_transferred = await client.post(
                f"/api/v1/compliance/personal-resources/knowledge_pack/{pack.json()['id']}/transfer",
                json={"target_user_id": str(target_id)},
                headers=admin,
            )
            assert pack_transferred.status_code == 200, pack_transferred.text

            target = await login(client, target_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=target)
            assert kb.json()["id"] in {row["id"] for row in bases.json()}
            packs = await client.get("/api/v1/knowledge-packs", headers=target)
            assert pack.json()["code"] in {row["code"] for row in packs.json()}

            disabled_login = await client.post(
                "/api/v1/auth/login",
                json={"email": owner_email, "password": "Password123!"},
            )
            assert disabled_login.status_code == 401
    finally:
        await cleanup_tenants([tenant_id])
