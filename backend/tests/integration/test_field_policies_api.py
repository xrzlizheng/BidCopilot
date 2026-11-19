import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.identity.models import User
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_asset_evaluation_uses_full_fields_while_display_is_masked() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("field-split-admin")
    _, engineer_id, _ = await seed_tenant_user(
        "field-split-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            company = await client.post(
                "/api/v1/companies", json={"name": "字段分离企业"}, headers=admin
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]
            policy = await client.put(
                "/api/v1/asset-field-policies",
                json={
                    "resource_type": "qualifications",
                    "field_name": "name",
                    "min_clearance": "confidential",
                    "mask_strategy": "hide",
                },
                headers=admin,
            )
            assert policy.status_code == 200, policy.text
            created = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": "核心涉密资质"},
                headers=admin,
            )
            assert created.status_code == 201, created.text

        async with SessionLocal() as session:
            admin_user = (
                await session.execute(select(User).where(User.id == admin_id))
            ).scalar_one()
            admin_user.clearance = "confidential"
            await session.commit()
            from app.modules.identity.service import load_user_context
            from app.modules.projects.service import (
                load_assets_for_evaluation,
                load_assets_for_user,
            )

            high = await load_user_context(session, admin_id)
            low = await load_user_context(session, engineer_id)
            assert high is not None and low is not None
            eval_low = await load_assets_for_evaluation(
                session, tenant_id, company_id, user=low
            )
            eval_high = await load_assets_for_evaluation(
                session, tenant_id, company_id, user=high
            )
            display_low = await load_assets_for_user(
                session, tenant_id, company_id, user=low
            )
            display_high = await load_assets_for_user(
                session, tenant_id, company_id, user=high
            )

        low_eval_names = {row["name"] for row in eval_low["qualifications"]}
        high_eval_names = {row["name"] for row in eval_high["qualifications"]}
        assert "核心涉密资质" in low_eval_names, "计算层不能因密级丢失资产"
        assert "核心涉密资质" in high_eval_names
        assert display_low["qualifications"][0]["name"] is None, "展示层必须脱敏"
        assert display_high["qualifications"][0]["name"] == "核心涉密资质"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_field_policy_masks_assets_until_clearance_matches() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("field-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)

            company = await client.post(
                "/api/v1/companies", json={"name": "字段策略企业"}, headers=admin
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]

            policy = await client.put(
                "/api/v1/asset-field-policies",
                json={
                    "resource_type": "qualifications",
                    "field_name": "cert_no",
                    "min_clearance": "confidential",
                    "mask_strategy": "mask",
                },
                headers=admin,
            )
            assert policy.status_code == 200, policy.text
            assert policy.json()["field_name"] == "cert_no"

            created = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "name": "涉密资质",
                    "cert_no": "CERT-123456",
                },
                headers=admin,
            )
            assert created.status_code == 201, created.text
            assert created.json()["cert_no"] == "***"

            listed = await client.get("/api/v1/assets/qualifications", headers=admin)
            assert listed.status_code == 200
            row = next(item for item in listed.json()["items"] if item["id"] == created.json()["id"])
            assert row["cert_no"] == "***"

            async with SessionLocal() as session:
                user = (
                    await session.execute(select(User).where(User.id == admin_id))
                ).scalar_one()
                user.clearance = "confidential"
                await session.commit()

            visible = await client.get("/api/v1/assets/qualifications", headers=admin)
            row = next(
                item for item in visible.json()["items"] if item["id"] == created.json()["id"]
            )
            assert row["cert_no"] == "CERT-123456"
    finally:
        await cleanup_tenants([tenant_id])
