from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(
                    select(Role).where(Role.tenant_id.is_(None), Role.code == code)
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    permissions=list(spec["permissions"]),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
            else:
                role.name = spec["name"]
                role.permissions = list(spec["permissions"])
            roles[code] = role
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
        session.add(
            UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id)
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_bid_result_retrospective_and_analytics() -> None:
    tenant_id, email = await _seed_tenant("bid-results")
    other_tenant_id, other_email = await _seed_tenant("bid-results-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            project_ids = []
            for name in ("复盘项目一", "复盘项目二", "复盘项目三"):
                created = await client.post(
                    "/api/v1/projects", json={"name": name}, headers=headers
                )
                assert created.status_code == 201, created.text
                project_ids.append(created.json()["id"])

            missing = await client.get(f"/api/v1/projects/{project_ids[0]}/result", headers=headers)
            assert missing.status_code == 404

            won = await client.put(
                f"/api/v1/projects/{project_ids[0]}/result",
                json={"outcome": "won", "win_amount": "120000.00", "competitor": "竞争对手甲"},
                headers=headers,
            )
            assert won.status_code == 200, won.text
            assert won.json()["outcome"] == "won"
            assert won.json()["loss_reason"] is None

            lost = await client.put(
                f"/api/v1/projects/{project_ids[1]}/result",
                json={"outcome": "lost", "loss_reason": "price", "notes": "报价高于对手 8%"},
                headers=headers,
            )
            assert lost.status_code == 200, lost.text
            assert lost.json()["win_amount"] is None

            cancelled = await client.put(
                f"/api/v1/projects/{project_ids[2]}/result",
                json={"outcome": "cancelled"},
                headers=headers,
            )
            assert cancelled.status_code == 200, cancelled.text

            project = await client.get(f"/api/v1/projects/{project_ids[0]}", headers=headers)
            assert project.json()["project"]["status"] == "won"

            analytics = await client.get("/api/v1/analytics/bid-results", headers=headers)
            assert analytics.status_code == 200, analytics.text
            body = analytics.json()
            assert body["summary"]["total"] == 3
            assert body["summary"]["won"] == 1
            assert body["summary"]["lost"] == 1
            assert body["summary"]["cancelled"] == 1
            assert body["summary"]["win_rate"] == 50.0
            assert body["summary"]["win_amount"] == "120000.00"
            assert body["by_loss_reason"] == [{"reason": "price", "label": "报价", "count": 1}]

            updated = await client.put(
                f"/api/v1/projects/{project_ids[0]}/result",
                json={"outcome": "lost", "loss_reason": "technical"},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["win_amount"] is None
            analytics = await client.get("/api/v1/analytics/bid-results", headers=headers)
            assert analytics.json()["summary"]["win_rate"] == 0.0

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden = await client.get(
                f"/api/v1/projects/{project_ids[0]}/result", headers=other_headers
            )
            assert forbidden.status_code == 404
            forbidden_put = await client.put(
                f"/api/v1/projects/{project_ids[0]}/result",
                json={"outcome": "won", "win_amount": "1"},
                headers=other_headers,
            )
            assert forbidden_put.status_code == 404

        async with SessionLocal() as session:
            actions = set(
                (
                    await session.execute(
                        select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
            assert "project.result.record" in actions
    finally:
        await _cleanup([tenant_id, other_tenant_id])
