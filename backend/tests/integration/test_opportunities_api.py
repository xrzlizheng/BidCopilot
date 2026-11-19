import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService
from app.modules.gateway.contract import DataResult, SourceStatus
from app.modules.gateway.deps import get_keeprisk_adapter
from app.modules.gateway.normalize import normalize_page
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "keeprisk"


class FakeAdapter:
    def __init__(self) -> None:
        self.list_payload = json.loads((FIXTURES / "e_007_1271_list.json").read_text(encoding="utf-8"))
        self.detail_payload = json.loads((FIXTURES / "e_007_1271_detail.json").read_text(encoding="utf-8"))
        self.detail_calls = 0

    async def fetch_page(self, query, *, page_no: int = 1) -> DataResult:
        items, warnings = normalize_page(self.list_payload["data"], fetched_at=datetime.now(UTC))
        return DataResult(
            status=SourceStatus.OK,
            data=items,
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://list",
            order_no="test-order-list",
            charged=True,
            warnings=warnings,
        )

    async def fetch_detail(self, ids: list[str]) -> DataResult:
        self.detail_calls += 1
        items, warnings = normalize_page(
            self.detail_payload["data"], fetched_at=datetime.now(UTC), include_content=True
        )
        return DataResult(
            status=SourceStatus.OK,
            data=items,
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://detail",
            order_no="test-order-detail",
            charged=True,
            warnings=warnings,
        )


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
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
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_opportunities_sync_detail_favorite_and_tenant_isolation() -> None:
    tenant_id, email = await _seed_tenant("tenant-a")
    other_tenant_id, other_email = await _seed_tenant("tenant-b")
    fake = FakeAdapter()
    app.dependency_overrides[get_keeprisk_adapter] = lambda: fake
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            refreshed = await client.post("/api/v1/auth/refresh")
            assert refreshed.status_code == 200, refreshed.text
            refreshed_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
            me = await client.get("/api/v1/auth/me", headers=refreshed_headers)
            assert me.status_code == 200

            sync = await client.post(
                "/api/v1/opportunities/sync",
                json={"keywords": ["系统集成"], "info_types": ["1"]},
                headers=headers,
            )
            assert sync.status_code == 200, sync.text
            assert sync.json()["status"] == "succeeded"
            assert sync.json()["rows_upserted"] == 3
            assert sync.json()["gateway_order_no"] == "test-order-list"

            listing = await client.get("/api/v1/opportunities", headers=headers)
            assert listing.status_code == 200, listing.text
            body = listing.json()
            assert body["total"] == 3
            assert body["items"][0]["detail_status"] == "list_only"
            opportunity_id = body["items"][0]["id"]

            detail = await client.get(f"/api/v1/opportunities/{opportunity_id}", headers=headers)
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert detail_body["detail_status"] == "full"
            assert detail_body["content_html"]
            assert detail_body["detail_error"] is None
            assert fake.detail_calls == 1

            detail_again = await client.get(
                f"/api/v1/opportunities/{opportunity_id}", headers=headers
            )
            assert detail_again.json()["detail_status"] == "full"
            assert fake.detail_calls == 1

            favorite = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/favorite", headers=headers
            )
            assert favorite.status_code == 200
            assert favorite.json() == {"favorited": True}
            favorite_list = await client.get(
                "/api/v1/opportunities?favorite=true", headers=headers
            )
            assert favorite_list.json()["total"] == 1
            repeat = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/favorite", headers=headers
            )
            assert repeat.json() == {"favorited": True}
            favorite_list = await client.get(
                "/api/v1/opportunities?favorite=true", headers=headers
            )
            assert favorite_list.json()["total"] == 1
            non_favorite_list = await client.get(
                "/api/v1/opportunities?favorite=false", headers=headers
            )
            assert all(item["id"] != opportunity_id for item in non_favorite_list.json()["items"])
            unfavorite = await client.delete(
                f"/api/v1/opportunities/{opportunity_id}/favorite", headers=headers
            )
            assert unfavorite.json() == {"favorited": False}
            favorite_list = await client.get(
                "/api/v1/opportunities?favorite=true", headers=headers
            )
            assert favorite_list.json()["total"] == 0

            audit = await client.get("/api/v1/audit/events", headers=headers)
            assert audit.status_code == 200, audit.text
            assert audit.json()["total"] >= 3
            async with SessionLocal() as session:
                assert await AuditService(session).verify_chain(tenant_id)

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden = await client.get(
                f"/api/v1/opportunities/{opportunity_id}", headers=other_headers
            )
            assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_sync_conflicts_when_same_query_locked() -> None:
    """同一查询并发触发同步必须 409；不同查询不受同一把锁影响。"""
    from app.db.locks import advisory_lock
    from app.modules.costs.service import query_hash
    from app.modules.opportunities.schemas import OpportunitySyncRequest
    from tests.support import cleanup_tenants, login, seed_tenant_user

    tenant_id, _, email = await seed_tenant_user("sync-lock")
    app.dependency_overrides[get_keeprisk_adapter] = lambda: FakeAdapter()
    payload = {"keywords": ["政务云"], "info_types": ["1", "2", "3"]}
    lock_key = (
        f"opportunity-sync:{tenant_id}:"
        f"{query_hash(OpportunitySyncRequest(**payload).model_dump(mode='json'))}"
    )
    try:
        async with advisory_lock(lock_key) as acquired:
            assert acquired is True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                headers = await login(client, email)
                conflict = await client.post(
                    "/api/v1/opportunities/sync", json=payload, headers=headers
                )
                assert conflict.status_code == 409, conflict.text
                assert "正在执行" in conflict.text

                other = await client.post(
                    "/api/v1/opportunities/sync",
                    json={"keywords": ["数据中心"], "info_types": ["1", "2", "3"]},
                    headers=headers,
                )
                assert other.status_code == 200, other.text
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])
