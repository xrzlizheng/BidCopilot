from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.db.session import SessionLocal
from app.main import app
from app.modules.identity.models import UserRole
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


@pytest.mark.integration
async def test_org_scoped_role_limits_access_and_expires() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("scope-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "scope-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)

            role = await client.post(
                "/api/v1/roles",
                json={
                    "name": "部门知识资产",
                    "permissions": [
                        "knowledge:read",
                        "knowledge:write",
                        "asset:read",
                        "asset:write",
                    ],
                    "scope_types": ["tenant", "org_unit"],
                },
                headers=admin,
            )
            assert role.status_code == 201, role.text

            org = await client.post(
                "/api/v1/org-units",
                json={"name": "范围内部门", "kind": "department"},
                headers=admin,
            )
            assert org.status_code == 201, org.text
            org_id = org.json()["id"]

            dept_kb = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "部门范围库", "scope": "org_unit", "org_unit_id": org_id},
                headers=admin,
            )
            assert dept_kb.status_code == 201, dept_kb.text
            dept_kb_id = dept_kb.json()["id"]

            company = await client.post(
                "/api/v1/companies", json={"name": "范围测试企业"}, headers=admin
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]
            enterprise_asset = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": "企业共享资质"},
                headers=admin,
            )
            assert enterprise_asset.status_code == 201, enterprise_asset.text

            assigned = await client.put(
                f"/api/v1/users/{engineer_id}/role-assignments",
                json={
                    "assignments": [
                        {
                            "role_code": role.json()["code"],
                            "scope_type": "org_unit",
                            "scope_id": org_id,
                        }
                    ]
                },
                headers=admin,
            )
            assert assigned.status_code == 200, assigned.text

            engineer = await login(client, engineer_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=engineer)
            assert bases.status_code == 200, bases.text
            base_ids = {row["id"] for row in bases.json()}
            assert dept_kb_id in base_ids
            assert all(row["scope"] == "org_unit" for row in bases.json())

            upload = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "dept.docx",
                        sample_tender_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"title": "部门资料", "kb_id": dept_kb_id},
                headers=engineer,
            )
            assert upload.status_code == 200, upload.text

            dept_asset = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "org_unit_id": org_id,
                    "name": "部门资质",
                },
                headers=engineer,
            )
            assert dept_asset.status_code == 201, dept_asset.text

            assets = await client.get("/api/v1/assets/qualifications", headers=engineer)
            assert assets.status_code == 200
            asset_ids = {row["id"] for row in assets.json()["items"]}
            assert dept_asset.json()["id"] in asset_ids
            assert enterprise_asset.json()["id"] not in asset_ids

            enterprise_attempt = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": "越权企业资质"},
                headers=engineer,
            )
            assert enterprise_attempt.status_code == 403

            async with SessionLocal() as session:
                await session.execute(
                    update(UserRole)
                    .where(UserRole.user_id == engineer_id)
                    .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
                )
                await session.commit()

            expired = await client.get("/api/v1/knowledge/bases", headers=engineer)
            assert expired.status_code == 403
            expired_assets = await client.get(
                "/api/v1/assets/qualifications", headers=engineer
            )
            assert expired_assets.status_code == 403
    finally:
        await cleanup_tenants([tenant_id])
