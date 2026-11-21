from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == "tenant_admin")
            )
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=None,
                code="tenant_admin",
                name="企业管理员",
                permissions=["tenant:read", "user:manage", "asset:*", "opportunity:*", "audit:read"],
                is_system=True,
            )
            session.add(role)
            await session.flush()
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name=label,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_assets_crud_import_expiry_and_tenant_isolation() -> None:
    tenant_id, email = await _seed_tenant("assets-a")
    other_tenant_id, other_email = await _seed_tenant("assets-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            company_resp = await client.post(
                "/api/v1/companies",
                json={"name": "测试集成商", "credit_code": "91510000TEST000001"},
                headers=headers,
            )
            assert company_resp.status_code == 201, company_resp.text
            company_id = company_resp.json()["id"]

            valid_to = (date.today() + timedelta(days=30)).isoformat()
            qualification = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "name": "信息安全等级保护",
                    "level": "三级",
                    "cert_no": "CERT-001",
                    "valid_to": valid_to,
                },
                headers=headers,
            )
            assert qualification.status_code == 201, qualification.text
            qualification_id = qualification.json()["id"]

            personnel_to = (date.today() + timedelta(days=60)).isoformat()
            personnel = await client.post(
                "/api/v1/assets/personnel",
                json={
                    "company_id": company_id,
                    "name": "张三",
                    "cert_type": "PMP",
                    "cert_no": "PMP-001",
                    "valid_to": personnel_to,
                },
                headers=headers,
            )
            assert personnel.status_code == 201, personnel.text

            listing = await client.get("/api/v1/assets/qualifications", headers=headers)
            assert listing.json()["total"] == 1

            filtered = await client.get(
                "/api/v1/assets/qualifications",
                params={"q": "信息"},
                headers=headers,
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["total"] == 1

            filtered_personnel = await client.get(
                "/api/v1/assets/personnel",
                params={"q": "张"},
                headers=headers,
            )
            assert filtered_personnel.status_code == 200, filtered_personnel.text
            assert filtered_personnel.json()["total"] == 1

            # 回归：用户输入里的 LIKE 通配符按字面量处理，单独一个 % 不得命中任何名称
            literal_wildcard = await client.get(
                "/api/v1/assets/qualifications",
                params={"q": "%"},
                headers=headers,
            )
            assert literal_wildcard.status_code == 200, literal_wildcard.text
            assert literal_wildcard.json()["total"] == 0

            expiring = await client.get("/api/v1/assets/expiring?days=90", headers=headers)
            assert expiring.status_code == 200, expiring.text
            assert expiring.json()["total"] >= 2

            csv_content = (
                "company_name,name,level,cert_no,valid_to\n"
                f"测试集成商,ISO9001,一级,ISO-001,{valid_to}\n"
                f"测试集成商,ISO27001,二级,ISO-002,{valid_to}\n"
                "测试集成商,,三级,BAD-001,\n"
            ).encode()
            imported = await client.post(
                "/api/v1/assets/import",
                data={"kind": "qualifications"},
                files={"file": ("assets.csv", csv_content, "text/csv")},
                headers=headers,
            )
            assert imported.status_code == 200, imported.text
            assert imported.json()["imported"] == 2
            assert imported.json()["failed"] == 1
            assert imported.json()["errors"][0]["row"] == 4

            listing = await client.get("/api/v1/assets/qualifications", headers=headers)
            assert listing.json()["total"] == 3

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden = await client.patch(
                f"/api/v1/companies/{company_id}",
                json={"name": "越权改名"},
                headers=other_headers,
            )
            assert forbidden.status_code == 404
            forbidden_delete = await client.delete(
                f"/api/v1/assets/qualifications/{qualification_id}", headers=other_headers
            )
            assert forbidden_delete.status_code == 404

            deleted = await client.delete(
                f"/api/v1/assets/qualifications/{qualification_id}", headers=headers
            )
            assert deleted.status_code == 204
            listing = await client.get("/api/v1/assets/qualifications", headers=headers)
            assert listing.json()["total"] == 2
    finally:
        await _cleanup([tenant_id, other_tenant_id])
