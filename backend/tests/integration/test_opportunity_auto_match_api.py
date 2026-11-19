from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.main import app
from app.modules.assets.models import Company, Qualification
from app.modules.notifications.models import Notification
from app.modules.opportunities.models import Opportunity, OpportunityMatch
from app.modules.projects.models import BidProject
from tests.support import cleanup_tenants, login, seed_tenant_user


async def _seed_opportunity(tenant_id: UUID, *, content_text: str | None) -> UUID:
    async with SessionLocal() as session:
        company = Company(
            tenant_id=tenant_id,
            name="示例科技有限公司",
            region="广东 广州",
            industry="政务",
            enterprise_industry="政务",
        )
        session.add(company)
        await session.flush()
        session.add(
            Qualification(
                tenant_id=tenant_id,
                company_id=company.id,
                dedup_key="qual-info-integration",
                name="信息系统集成及服务资质",
                level="二级",
                valid_to=date(2030, 1, 1),
                source="manual",
            )
        )
        opportunity = Opportunity(
            tenant_id=tenant_id,
            source="keeprisk:E_007_1271",
            dedup_key="auto-match-opportunity",
            title="某市政务数据中心集成项目",
            region="广东 广州",
            industry=["政务"],
            budget_amount=5_000_000,
            info_type="tender",
            detail_status="full" if content_text else "list_only",
            confidence="high",
            content_text=content_text,
            bid_deadline=datetime(2026, 12, 31, tzinfo=UTC),
            fetched_at=datetime.now(UTC),
        )
        session.add(opportunity)
        await session.commit()
        await session.refresh(opportunity)
        return opportunity.id


CONTENT = """第一章 招标公告
二、申请人的资格要求：
1. 具有信息系统集成及服务资质二级及以上。
三、评标办法
综合评分法。
"""


@pytest.mark.integration
async def test_auto_match_persists_settings_and_idempotent_project() -> None:
    tenant_id, _, email = await seed_tenant_user("auto-match")
    other_tenant_id, _, other_email = await seed_tenant_user("auto-match-other")
    opportunity_id = await _seed_opportunity(tenant_id, content_text=CONTENT)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)

            settings = await client.get("/api/v1/opportunities/auto-match/settings", headers=headers)
            assert settings.status_code == 200, settings.text
            assert settings.json()["source"] == "default"
            assert settings.json()["auto_create_project"] is False

            patched = await client.patch(
                "/api/v1/opportunities/auto-match/settings",
                json={"auto_create_project": True, "recommend_min_score": 50, "skip_below_score": 20},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["auto_create_project"] is True
            assert patched.json()["recommend_min_score"] == 50

            first = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/match", headers=headers
            )
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["decision"] == "recommend"
            assert body["qualified"] is True
            assert body["match_score"] >= 50
            assert body["engine_version"] == "heuristic-rules-v1"
            assert body["qualification_gaps"] == []
            assert body["project_id"]

            second = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/match", headers=headers
            )
            assert second.status_code == 200, second.text
            assert second.json()["project_id"] == body["project_id"]

            stored = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/match", headers=headers
            )
            assert stored.status_code == 200, stored.text
            assert stored.json()["match_score"] == body["match_score"]

            notifications = await client.get("/api/v1/notifications", headers=headers)
            assert notifications.status_code == 200
            assert any(
                item["type"] == "opportunity_match_recommend"
                for item in notifications.json()["items"]
            )

            outsider = await login(client, other_email)
            cross_tenant = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/match", headers=outsider
            )
            assert cross_tenant.status_code == 404

        async with SessionLocal() as session:
            project_count = (
                await session.execute(
                    select(func.count())
                    .select_from(BidProject)
                    .where(
                        BidProject.tenant_id == tenant_id,
                        BidProject.opportunity_id == opportunity_id,
                    )
                )
            ).scalar_one()
            assert project_count == 1
            match = (
                await session.execute(
                    select(OpportunityMatch).where(
                        OpportunityMatch.tenant_id == tenant_id,
                        OpportunityMatch.opportunity_id == opportunity_id,
                    )
                )
            ).scalar_one()
            assert match.project_id is not None
            assert match.engine_version == "heuristic-rules-v1"
            notification_count = (
                await session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.tenant_id == tenant_id,
                        Notification.type == "opportunity_match_recommend",
                    )
                )
            ).scalar_one()
            assert notification_count == 1
    finally:
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_auto_match_marks_unknown_without_content_and_batch_runs() -> None:
    tenant_id, _, email = await seed_tenant_user("auto-match-unknown")
    opportunity_id = await _seed_opportunity(tenant_id, content_text=None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            matched = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/match", headers=headers
            )
            assert matched.status_code == 200, matched.text
            body = matched.json()
            assert body["qualified"] is None
            assert body["decision"] == "review"
            assert body["qualification_gaps"][0]["status"] == "UNKNOWN"
            assert body["project_id"] is None

            batch = await client.post(
                "/api/v1/opportunities/auto-match/run",
                json={"limit": 20, "force": True},
                headers=headers,
            )
            assert batch.status_code == 200, batch.text
            result = batch.json()
            assert result["enabled"] is True
            assert result["scanned"] == 1
            assert result["matched"] == 1
            assert result["review"] == 1
            assert result["projects_created"] == 0
    finally:
        await cleanup_tenants([tenant_id])
