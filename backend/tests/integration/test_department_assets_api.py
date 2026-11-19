import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_department_assets_are_scoped_and_manager_writable() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("asset-org-admin")
    _, manager_id, manager_email = await seed_tenant_user(
        "asset-org-manager", "bid_engineer", tenant_id
    )
    _, member_id, member_email = await seed_tenant_user(
        "asset-org-member", "bid_engineer", tenant_id
    )
    _, _, outsider_email = await seed_tenant_user(
        "asset-org-outsider", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            manager = await login(client, manager_email)
            member = await login(client, member_email)
            outsider = await login(client, outsider_email)

            company = await client.post(
                "/api/v1/companies", json={"name": "测试投标企业"}, headers=admin
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]

            org = await client.post(
                "/api/v1/org-units",
                json={"name": "资产一部", "kind": "department"},
                headers=admin,
            )
            assert org.status_code == 201, org.text
            org_id = org.json()["id"]
            for user_id, role in ((manager_id, "manager"), (member_id, "member")):
                added = await client.post(
                    f"/api/v1/org-units/{org_id}/members",
                    json={"user_id": str(user_id), "member_role": role},
                    headers=admin,
                )
                assert added.status_code == 201, added.text

            created = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "org_unit_id": org_id,
                    "name": "部门专用资质",
                    "level": "乙级",
                },
                headers=manager,
            )
            assert created.status_code == 201, created.text
            asset_id = created.json()["id"]
            assert created.json()["org_unit_id"] == org_id

            member_list = await client.get(
                "/api/v1/assets/qualifications", headers=member
            )
            assert member_list.status_code == 200
            assert asset_id in {row["id"] for row in member_list.json()["items"]}

            outsider_list = await client.get(
                "/api/v1/assets/qualifications", headers=outsider
            )
            assert outsider_list.status_code == 200
            assert asset_id not in {row["id"] for row in outsider_list.json()["items"]}

            member_patch = await client.patch(
                f"/api/v1/assets/qualifications/{asset_id}",
                json={"name": "成员越权修改"},
                headers=member,
            )
            assert member_patch.status_code == 403

            outsider_patch = await client.patch(
                f"/api/v1/assets/qualifications/{asset_id}",
                json={"name": "外部越权修改"},
                headers=outsider,
            )
            assert outsider_patch.status_code == 404

            manager_patch = await client.patch(
                f"/api/v1/assets/qualifications/{asset_id}",
                json={"name": "部门经理修改"},
                headers=manager,
            )
            assert manager_patch.status_code == 200, manager_patch.text
            assert manager_patch.json()["name"] == "部门经理修改"

            admin_list = await client.get(
                "/api/v1/assets/qualifications", headers=admin
            )
            assert asset_id in {row["id"] for row in admin_list.json()["items"]}

            enterprise = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": "企业共享资质"},
                headers=admin,
            )
            assert enterprise.status_code == 201, enterprise.text
            outsider_enterprise = await client.get(
                "/api/v1/assets/qualifications", headers=outsider
            )
            assert enterprise.json()["id"] in {
                row["id"] for row in outsider_enterprise.json()["items"]
            }

            deleted = await client.delete(
                f"/api/v1/assets/qualifications/{asset_id}", headers=manager
            )
            assert deleted.status_code == 204
            member_after = await client.get(
                "/api/v1/assets/qualifications", headers=member
            )
            assert asset_id not in {row["id"] for row in member_after.json()["items"]}
    finally:
        await cleanup_tenants([tenant_id])
