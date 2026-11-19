from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.notifications.models import Notification
from app.modules.projects.models import ProjectReminder
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_project_deadline_reminders_are_idempotent_and_skip_empty_deadline() -> None:
    tenant_id, admin_id, email = await seed_tenant_user("reminders")
    try:
        now = datetime.now(UTC)
        bid_deadline = now + timedelta(days=3, hours=-1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects",
                json={"name": "提醒项目", "bid_deadline": bid_deadline.isoformat()},
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            patched = await client.patch(
                f"/api/v1/projects/{project_id}",
                json={"internal_deadline": (now - timedelta(days=1)).isoformat()},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text

            no_deadline = await client.post(
                "/api/v1/projects", json={"name": "无截止项目"}, headers=headers
            )
            no_deadline_id = no_deadline.json()["id"]

        from app.modules.projects.reminders import scan_due_project_reminders

        async with SessionLocal() as session:
            first = await scan_due_project_reminders(
                session, now=now, tenant_id=tenant_id
            )
            second = await scan_due_project_reminders(
                session, now=now, tenant_id=tenant_id
            )
            rows = list(
                (
                    await session.execute(
                        select(ProjectReminder)
                        .where(ProjectReminder.tenant_id == tenant_id)
                        .order_by(ProjectReminder.offset_days.desc())
                    )
                ).scalars()
            )
            notifications = list(
                (
                    await session.execute(
                        select(Notification).where(
                            Notification.tenant_id == tenant_id,
                            Notification.user_id == admin_id,
                        )
                    )
                ).scalars()
            )

        assert first >= 2, "bid deadline 7/3 day reminders should fire"
        assert second == 0, "same node/offset must not send twice"
        assert {row.offset_days for row in rows} == {7, 3}
        assert all(row.status == "sent" for row in rows)
        assert len(notifications) >= 2
        assert all(row.project_id != no_deadline_id for row in rows)
    finally:
        await cleanup_tenants([tenant_id])
