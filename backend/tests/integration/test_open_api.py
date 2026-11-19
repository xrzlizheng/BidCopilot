"""开放 API：API Token 认证与 Webhook 订阅/投递。

覆盖口径：
- Token 明文只在创建响应出现一次；Bearer btc_... 走独立认证分支；
  生效权限 = scopes ∩ 创建者权限（不放大）；吊销/过期立即 401。
- Webhook 只允许 https 且拒绝内网地址（SSRF）；secret 不明文入库；
  投递带 HMAC-SHA256 签名，结果写 delivery 留痕，失败不冒泡。
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.api_token_models import ApiToken
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.notifications import webhooks
from app.modules.notifications.models import Notification
from app.modules.notifications.webhook_models import WebhookDelivery, WebhookEndpoint


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
        await session.execute(delete(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant_id))
        await session.execute(
            delete(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id)
        )
        await session.execute(delete(ApiToken).where(ApiToken.tenant_id == tenant_id))
        await session.execute(delete(Notification).where(Notification.tenant_id == tenant_id))
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.integration
async def test_api_token_lifecycle_and_bearer_auth() -> None:
    tenant_id, email = await _seed_admin("open-api-token")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            empty = await client.get("/api/v1/api-tokens", headers=headers)
            assert empty.status_code == 200, empty.text
            assert empty.json() == []

            created = await client.post(
                "/api/v1/api-tokens",
                json={"name": "集成商 A", "scopes": ["project:read"], "expires_in_days": 30},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            assert payload["token"].startswith("btc_")
            assert payload["name"] == "集成商 A"
            assert payload["scopes"] == ["project:read"]
            assert payload["expires_at"] is not None
            plaintext = payload["token"]

            # 明文只在创建响应出现一次：列表不含 token 字段。
            listed = await client.get("/api/v1/api-tokens", headers=headers)
            assert listed.status_code == 200, listed.text
            row = listed.json()[0]
            assert "token" not in row
            assert row["token_prefix"] == plaintext[:12]

            # Token 认证：scope 内的端点放行。
            token_headers = {"Authorization": f"Bearer {plaintext}"}
            projects = await client.get("/api/v1/projects", headers=token_headers)
            assert projects.status_code == 200, projects.text

            # scope 外的端点 403（权限不放大）。
            forbidden = await client.post(
                "/api/v1/projects",
                json={"name": "x", "tender_no": "x"},
                headers=token_headers,
            )
            assert forbidden.status_code == 403, forbidden.text

            # 吊销后立即失效。
            revoked = await client.delete(f"/api/v1/api-tokens/{row['id']}", headers=headers)
            assert revoked.status_code == 204, revoked.text
            after = await client.get("/api/v1/projects", headers=token_headers)
            assert after.status_code == 401, after.text

            listed_after = await client.get("/api/v1/api-tokens", headers=headers)
            assert listed_after.json() == []
    finally:
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_api_token_cross_tenant_is_404() -> None:
    tenant_id, email = await _seed_admin("open-api-token-a")
    other_tenant_id, other_email = await _seed_admin("open-api-token-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            other_headers = await _login(client, other_email)

            mine = await client.post(
                "/api/v1/api-tokens",
                json={"name": "mine", "scopes": ["project:read"]},
                headers=headers,
            )
            assert mine.status_code == 201, mine.text
            token_id = mine.json()["id"]

            # 别租户按 ID 吊销：404，不泄露存在性。
            cross = await client.delete(f"/api/v1/api-tokens/{token_id}", headers=other_headers)
            assert cross.status_code == 404, cross.text

            unknown = await client.delete(f"/api/v1/api-tokens/{uuid4()}", headers=headers)
            assert unknown.status_code == 404, unknown.text
    finally:
        await _cleanup(tenant_id)
        await _cleanup(other_tenant_id)


@pytest.mark.integration
async def test_api_token_expired_is_rejected() -> None:
    tenant_id, email = await _seed_admin("open-api-token-expired")
    try:
        async with SessionLocal() as session:
            admin = (
                await session.execute(select(User).where(User.tenant_id == tenant_id))
            ).scalars().first()
            token = ApiToken(
                tenant_id=tenant_id,
                name="expired",
                token_prefix="btc_expired01",
                token_hash=hashlib.sha256(b"btc_expired-value").hexdigest(),
                scopes=["project:read"],
                created_by=admin.id,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(token)
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/projects", headers={"Authorization": "Bearer btc_expired-value"}
            )
            assert response.status_code == 401, response.text
    finally:
        await _cleanup(tenant_id)


def _capture_transport(
    captured: list[dict[str, Any]], *, status: int = 200
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": request.content,
            }
        )
        return httpx.Response(status, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.mark.integration
async def test_webhook_lifecycle_ssrf_and_signed_delivery() -> None:
    tenant_id, email = await _seed_admin("open-api-webhook")
    captured: list[dict[str, Any]] = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            # 非 https 与内网地址一律 422。
            for bad in ("http://example.com/hook", "https://127.0.0.1/hook", "https://10.0.0.1/h"):
                rejected = await client.post(
                    "/api/v1/webhooks",
                    json={"url": bad, "events": ["project.status.changed"]},
                    headers=headers,
                )
                assert rejected.status_code == 422, (bad, rejected.text)

            unknown_event = await client.post(
                "/api/v1/webhooks",
                json={"url": "https://example.com/hook", "events": ["not.a.real.event"]},
                headers=headers,
            )
            assert unknown_event.status_code == 422, unknown_event.text

            created = await client.post(
                "/api/v1/webhooks",
                json={
                    "url": "https://example.com/hook",
                    "events": ["project.status.changed"],
                    "secret": "s" * 32,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            endpoint = created.json()
            assert endpoint["secret"] == "s" * 32
            endpoint_id = endpoint["id"]

            # 列表不返回 secret。
            listed = await client.get("/api/v1/webhooks", headers=headers)
            assert listed.status_code == 200, listed.text
            assert "secret" not in listed.json()[0]

            duplicate = await client.post(
                "/api/v1/webhooks",
                json={"url": "https://example.com/hook", "events": ["project.status.changed"]},
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text

            # 投递：签名可被接收方按同一 secret 复算；结果写留痕。
            async with SessionLocal() as session:
                deliveries = await webhooks.dispatch_event(
                    session,
                    tenant_id,
                    "project.status.changed",
                    {"project_id": str(uuid4()), "from_status": "draft", "to_status": "active"},
                    transport=_capture_transport(captured),
                )
            assert len(deliveries) == 1
            assert deliveries[0].status == "success"
            assert deliveries[0].attempts == 1
            assert deliveries[0].response_code == 200

            assert len(captured) == 1
            request = captured[0]
            signature = request["headers"]["x-bidcopilot-signature"]
            timestamp = request["headers"]["x-bidcopilot-timestamp"]
            body = request["body"]
            expected = hmac.new(
                (b"s" * 32),
                f"{timestamp}.".encode() + body,
                hashlib.sha256,
            ).hexdigest()
            assert signature == f"t={timestamp},v1={expected}"
            payload = json.loads(body)
            assert payload["event"] == "project.status.changed"
            assert payload["data"]["to_status"] == "active"

            deliveries_listed = await client.get(
                f"/api/v1/webhooks/{endpoint_id}/deliveries", headers=headers
            )
            assert deliveries_listed.status_code == 200, deliveries_listed.text
            assert deliveries_listed.json()[0]["status"] == "success"

            # 未订阅的事件不投递。
            async with SessionLocal() as session:
                none = await webhooks.dispatch_event(
                    session,
                    tenant_id,
                    "compliance.hard_finding",
                    {"project_id": str(uuid4()), "hard_count": 1},
                    transport=_capture_transport(captured),
                )
            assert none == []
            assert len(captured) == 1

            removed = await client.delete(f"/api/v1/webhooks/{endpoint_id}", headers=headers)
            assert removed.status_code == 204, removed.text
            remaining = await client.get("/api/v1/webhooks", headers=headers)
            assert remaining.json() == []
    finally:
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_webhook_failed_delivery_is_recorded() -> None:
    tenant_id, email = await _seed_admin("open-api-webhook-fail")
    captured: list[dict[str, Any]] = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await client.post(
                "/api/v1/webhooks",
                json={"url": "https://example.com/hook", "events": ["*"], "secret": "f" * 32},
                headers=headers,
            )
            assert created.status_code == 201, created.text

            async with SessionLocal() as session:
                deliveries = await webhooks.dispatch_event(
                    session,
                    tenant_id,
                    "submission_receipt.created",
                    {"project_id": str(uuid4()), "receipt_id": str(uuid4())},
                    transport=_capture_transport(captured, status=500),
                )
            assert len(deliveries) == 1
            assert deliveries[0].status == "failed"
            assert deliveries[0].response_code == 500
            assert deliveries[0].error == "HTTP 500"
            # 默认 3 次尝试后仍失败：留痕记录 attempts，不向调用方抛异常。
            assert deliveries[0].attempts == 3
            assert len(captured) == 3
    finally:
        await _cleanup(tenant_id)
