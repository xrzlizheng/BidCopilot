from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.gateway.contract import DataResult, SourceStatus
from app.modules.gateway.deps import get_enterprise_intel_adapter
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.notifications import channels
from app.modules.notifications.models import Notification


def _payload(risk: str, count: int) -> dict:
    return {
        "sections": [
            {
                "key": "enforced",
                "title": "被执行人",
                "status": "ok",
                "digest": {
                    "risk_level": risk,
                    "count": count,
                    "has_records": True,
                    "summary": f"被执行人 {count} 条",
                },
            }
        ],
        "risk_summary": {"overall_level": risk, "red_count": 1 if risk == "red" else 0},
    }


class FakeWatchlistAdapter(EnterpriseIntelAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.payloads = [_payload("green", 0), _payload("red", 1)]

    async def due_diligence(self, ent_name: str, **kwargs) -> DataResult[dict]:
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return DataResult(
            status=SourceStatus.OK,
            data=payload,
            source="keeprisk-mcp",
            fetched_at=datetime.now(UTC),
            charged=True,
            api_calls=4,
            billing_units=4,
        )


async def _seed_admin(label: str) -> tuple[UUID, str]:
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
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"admin-{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name="Admin",
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=roles["tenant_admin"].id,
                scope_type="tenant",
                scope_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Notification).where(Notification.tenant_id == tenant_id))
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_watchlist_run_notifies_on_risk_change() -> None:
    tenant_id, email = await _seed_admin("intel-watchlist")
    fake = FakeWatchlistAdapter()
    app.dependency_overrides[get_enterprise_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            created = await client.post(
                "/api/v1/company-intel/watchlist",
                json={"ent_name": "示例科技有限公司", "label": "示例采购人"},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            item_id = created.json()["id"]

            first = await client.post(
                f"/api/v1/company-intel/watchlist/{item_id}/run", headers=headers
            )
            assert first.status_code == 200, first.text
            assert first.json()["item"]["last_status"] == "ok"
            assert first.json()["item"]["last_digest"]["_risk"] == "green"

            second = await client.post(
                f"/api/v1/company-intel/watchlist/{item_id}/run", headers=headers
            )
            assert second.status_code == 200, second.text
            assert second.json()["item"]["last_digest"]["_risk"] == "red"

        async with SessionLocal() as session:
            notification = (
                await session.execute(
                    select(Notification).where(
                        Notification.tenant_id == tenant_id,
                        Notification.type == "intel_watchlist",
                    )
                )
            ).scalar_one()
            assert "red" in (notification.body or "")
            assert notification.title.startswith("监控主体风险变化")
    finally:
        app.dependency_overrides.pop(get_enterprise_intel_adapter, None)
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_watchlist_risk_notification_dispatches_to_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, email = await _seed_admin("intel-watchlist-dispatch")
    sent: list[tuple[str, str, str | None]] = []

    async def fake_send(target: str, title: str, body: str | None) -> None:
        sent.append((target, title, body))

    monkeypatch.setattr(channels, "_send_wechat", fake_send)
    fake = FakeWatchlistAdapter()
    app.dependency_overrides[get_enterprise_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            channel = await client.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "wechat",
                    "target": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=dispatch",
                    "label": "监控群",
                },
                headers=headers,
            )
            assert channel.status_code == 201, channel.text

            created = await client.post(
                "/api/v1/company-intel/watchlist",
                json={"ent_name": "示例科技有限公司", "label": "示例采购人"},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            item_id = created.json()["id"]

            first = await client.post(
                f"/api/v1/company-intel/watchlist/{item_id}/run", headers=headers
            )
            assert first.status_code == 200, first.text
            # 首次建立基线不产生风险变化通知
            assert sent == []

            second = await client.post(
                f"/api/v1/company-intel/watchlist/{item_id}/run", headers=headers
            )
            assert second.status_code == 200, second.text
            assert second.json()["item"]["last_digest"]["_risk"] == "red"

        assert len(sent) == 1
        target, title, body = sent[0]
        assert target.endswith("key=dispatch")
        assert title.startswith("监控主体风险变化")
        assert body and "red" in body
    finally:
        app.dependency_overrides.pop(get_enterprise_intel_adapter, None)
        monkeypatch.undo()
        await _cleanup(tenant_id)
