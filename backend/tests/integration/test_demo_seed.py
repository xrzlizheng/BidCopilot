from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.demo import seed_demo
from app.main import app
from app.modules.assets.models import Company, Credit, Performance, Personnel, Qualification
from app.modules.audit.models import AuditEvent
from app.modules.generation.models import BidSection
from app.modules.identity.models import Tenant
from app.modules.projects.models import BidProject, BidResult, ScoringItem, TenderConstraint


async def _count(model, tenant_id: UUID) -> int:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )
        ).scalar_one()


@pytest.mark.integration
async def test_demo_seed_is_idempotent_and_login_ready() -> None:
    suffix = uuid4().hex[:10]
    slug = f"demo-{suffix}"
    email = f"demo-{suffix}@example.com"
    async with SessionLocal() as session:
        first = await seed_demo(
            session,
            tenant_name="演示投标企业",
            tenant_slug=slug,
            email=email,
            password="Password123!",
        )
    async with SessionLocal() as session:
        second = await seed_demo(
            session,
            tenant_name="演示投标企业",
            tenant_slug=slug,
            email=email,
            password="Password123!",
        )

    try:
        assert first.tenant_id == second.tenant_id
        assert first.project_ids == second.project_ids
        assert await _count(Company, first.tenant_id) == 1
        assert await _count(Qualification, first.tenant_id) == 1
        assert await _count(Personnel, first.tenant_id) == 1
        assert await _count(Performance, first.tenant_id) == 1
        assert await _count(Credit, first.tenant_id) == 1
        assert await _count(BidProject, first.tenant_id) == 2
        assert await _count(TenderConstraint, first.tenant_id) == 1
        assert await _count(ScoringItem, first.tenant_id) == 3
        assert await _count(BidSection, first.tenant_id) == 5
        assert await _count(BidResult, first.tenant_id) == 2

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            projects = await client.get("/api/v1/projects", headers=headers)
            assert projects.status_code == 200, projects.text
            assert len(projects.json()) == 2
            analytics = await client.get("/api/v1/analytics/bid-results", headers=headers)
            assert analytics.status_code == 200, analytics.text
            summary = analytics.json()["summary"]
            assert summary["total"] == 2
            assert summary["won"] == 1
            assert summary["lost"] == 1
            assert summary["win_rate"] == 50.0
            assert summary["win_amount"] == "1980000.00"
            detail = await client.get(f"/api/v1/projects/{first.project_ids[0]}", headers=headers)
            assert detail.status_code == 200, detail.text
            payload = detail.json()["latest_constraint"]["payload"]
            assert len(payload["scoring_items"]) == 3
            assert payload["parse_meta"]["parser_version"] == "demo"
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == first.tenant_id))
            await session.execute(delete(Tenant).where(Tenant.id == first.tenant_id))
            await session.commit()
