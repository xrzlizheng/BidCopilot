from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.gateway.company_adapter import (
    BIDS_SOURCE,
    PROFILE_SOURCE,
    CompanyProfileEntry,
    CompanyProfilePage,
)
from app.modules.gateway.contract import DataError, DataResult, SourceStatus
from app.modules.gateway.deps import get_company_intel_adapter
from app.modules.gateway.normalize import NormalizedOpportunity
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


def _notice(uid: str, title: str, amount: Decimal) -> NormalizedOpportunity:
    fetched_at = datetime(2026, 9, 1, tzinfo=UTC)
    return NormalizedOpportunity(
        source=BIDS_SOURCE,
        source_id=uid,
        dedup_key=uid,
        title=title,
        project_code=f"P-{uid}",
        project_name=title,
        buyer="某采购人",
        agency=None,
        winner="测试科技有限公司",
        region="辽宁省",
        industry=["信息通信"],
        budget_amount=None,
        win_bid_amount=amount,
        publish_at=fetched_at,
        info_type="award",
        source_url=f"https://example.com/{uid}",
        detail_status="list_only",
        confidence="high",
        content_html=None,
        content_text=None,
        content_fetched_at=None,
        fetched_at=fetched_at,
    )


class FakeCompanyIntelAdapter:
    def __init__(self) -> None:
        self.profile_calls = 0
        self.bid_roles: list[str] = []

    async def fetch_profile(self, company_name: str) -> DataResult[CompanyProfilePage]:
        self.profile_calls += 1
        entry = CompanyProfileEntry(
            subject_name=company_name,
            subject_status="存续",
            subject_type="有限责任公司",
            region="辽宁省 盘锦市",
            roles=["tenderer", "winner"],
            related_count=12,
            bidder_count=3,
            winner_count=2,
            agent_count=1,
            tender_count=6,
            latest_publish_at=datetime(2026, 9, 1, tzinfo=UTC),
            latest_title="某项目中标公告",
            latest_role="中标方",
            info_type="3",
        )
        return DataResult(
            status=SourceStatus.OK,
            data=CompanyProfilePage(total=1, entries=[entry]),
            source=PROFILE_SOURCE,
            fetched_at=datetime.now(UTC),
            order_no=f"profile-order-{self.profile_calls}",
            charged=True,
        )

    async def fetch_bids(
        self, company_name: str, *, role: str, page_size: int = 50
    ) -> DataResult[list[NormalizedOpportunity]]:
        self.bid_roles.append(role)
        if role != "winner":
            return DataResult(
                status=SourceStatus.EMPTY,
                data=[],
                source=BIDS_SOURCE,
                fetched_at=datetime.now(UTC),
                order_no=f"{role}-order-{len(self.bid_roles)}",
            )
        return DataResult(
            status=SourceStatus.OK,
            data=[
                _notice("n1", "某项目中标公告一", Decimal("1298520.00")),
                _notice("n2", "某项目中标公告二", Decimal("880000.00")),
            ],
            source=BIDS_SOURCE,
            fetched_at=datetime.now(UTC),
            order_no=f"bids-order-{len(self.bid_roles)}",
            charged=True,
        )


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
async def test_company_intel_profile_and_bid_sync() -> None:
    tenant_id, email = await _seed_tenant("company-intel")
    other_tenant_id, other_email = await _seed_tenant("company-intel-other")
    fake = FakeCompanyIntelAdapter()
    app.dependency_overrides[get_company_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            company_resp = await client.post(
                "/api/v1/companies",
                json={"name": "测试科技有限公司"},
                headers=headers,
            )
            assert company_resp.status_code == 201, company_resp.text
            company_id = company_resp.json()["id"]

            profile = await client.get(f"/api/v1/companies/{company_id}/profile", headers=headers)
            assert profile.status_code == 200, profile.text
            body = profile.json()
            assert body["status"] == "ok"
            assert body["charged"] is True
            assert body["order_no"] == "profile-order-1"
            assert body["entries"][0]["winner_count"] == 2
            assert body["entries"][0]["roles"] == ["tenderer", "winner"]

            synced = await client.post(
                f"/api/v1/companies/{company_id}/bids/sync",
                json={"roles": ["winner", "tenderer"]},
                headers=headers,
            )
            assert synced.status_code == 200, synced.text
            sync_body = synced.json()
            assert sync_body["upserted"] == 2
            assert [item["role"] for item in sync_body["results"]] == ["winner", "tenderer"]
            assert [item["status"] for item in sync_body["results"]] == ["ok", "empty"]

            bids = await client.get(
                f"/api/v1/companies/{company_id}/bids?role=winner", headers=headers
            )
            assert bids.status_code == 200, bids.text
            assert bids.json()["total"] == 2
            first = bids.json()["items"][0]
            assert first["title"] in {"某项目中标公告一", "某项目中标公告二"}
            assert first["source"] == BIDS_SOURCE
            assert first["win_bid_amount"] is not None

            resynced = await client.post(
                f"/api/v1/companies/{company_id}/bids/sync",
                json={"roles": ["winner"]},
                headers=headers,
            )
            assert resynced.status_code == 200, resynced.text
            assert resynced.json()["upserted"] == 2
            bids = await client.get(f"/api/v1/companies/{company_id}/bids", headers=headers)
            assert bids.json()["total"] == 2

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden_profile = await client.get(
                f"/api/v1/companies/{company_id}/profile", headers=other_headers
            )
            assert forbidden_profile.status_code == 404
            forbidden_sync = await client.post(
                f"/api/v1/companies/{company_id}/bids/sync",
                json={"roles": ["winner"]},
                headers=other_headers,
            )
            assert forbidden_sync.status_code == 404

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
            assert "asset.company.profile" in actions
            assert "asset.company.bids.sync" in actions
    finally:
        app.dependency_overrides.pop(get_company_intel_adapter, None)
        await _cleanup([tenant_id, other_tenant_id])


class _RateLimitedAdapter(FakeCompanyIntelAdapter):
    async def fetch_profile(self, company_name: str) -> DataResult[CompanyProfilePage]:
        return DataResult(
            status=SourceStatus.RATE_LIMITED,
            data=None,
            source=PROFILE_SOURCE,
            fetched_at=datetime.now(UTC),
            error=DataError(code="104", message="IP 请求频率受限"),
            order_no="rl-order",
            charged=False,
        )


@pytest.mark.integration
async def test_company_intel_profile_surfaces_non_ok_status() -> None:
    tenant_id, email = await _seed_tenant("company-intel-rl")
    fake = _RateLimitedAdapter()
    app.dependency_overrides[get_company_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            company_resp = await client.post(
                "/api/v1/companies", json={"name": "受限科技有限公司"}, headers=headers
            )
            company_id = company_resp.json()["id"]

            profile = await client.get(f"/api/v1/companies/{company_id}/profile", headers=headers)
            assert profile.status_code == 200, profile.text
            body = profile.json()
            assert body["status"] == "rate_limited"
            assert body["entries"] == []
            assert body["charged"] is False
            assert body["order_no"] == "rl-order"
            assert body["error"] is not None and body["error"]["code"] == "104"
    finally:
        app.dependency_overrides.pop(get_company_intel_adapter, None)
        await _cleanup([tenant_id])
