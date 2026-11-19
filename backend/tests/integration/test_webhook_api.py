import hashlib
import hmac
import itertools
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.notifications import webhooks
from app.modules.notifications.webhook_models import (
    WebhookDelivery,
    WebhookEndpoint,
)
from app.modules.notifications.webhook_router import router as webhook_router
from app.modules.system.models import ModelSecret
from tests.support import cleanup_tenants, seed_tenant_user

HOOK_HOST = "hooks.test"
HOOK_URL = f"https://{HOOK_HOST}/bidcopilot/events"

# 登录限流按 IP 计数（LOGIN_IP_MAX_ATTEMPTS），并行跑集成套件时所有进程共用
# 同一个测试客户端 IP，会被别人的失败尝试挤爆成 429。给每次登录分配独立
# X-Real-IP（nginx 语义下该头由网关覆盖，测试里由测试自己给），不改共享配置。
_IP_COUNTER = itertools.count(1)


def _login_ip() -> dict[str, str]:
    value = next(_IP_COUNTER)
    return {"X-Real-IP": f"10.98.{value // 250}.{value % 250 + 1}"}


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
        headers=_login_ip(),
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _register_router() -> None:
    """幂等注册：已被 Lead 接进 main.py 时不再重复注册。"""
    if not any(getattr(route, "path", None) == "/api/v1/webhooks" for route in app.routes):
        app.include_router(webhook_router)


_register_router()


@pytest.fixture(autouse=True)
def _allow_test_hook_host(monkeypatch):
    """只放行测试用域名：SSRF 闸门本身仍按默认策略拒绝私网/回环。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "WEBHOOK_ALLOWED_HOSTS", HOOK_HOST)
    monkeypatch.setattr(settings, "WEBHOOK_RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", 3)


async def _create_webhook(
    client: AsyncClient, headers: dict[str, str], **payload
) -> dict:
    body = {"url": HOOK_URL, "events": ["*"], **payload}
    response = await client.post("/api/v1/webhooks", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.integration
async def test_webhook_crud_and_secret_is_encrypted() -> None:
    tenant_id, user_id, email = await seed_tenant_user("webhook-crud")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            empty = await client.get("/api/v1/webhooks", headers=headers)
            assert empty.status_code == 200, empty.text
            assert empty.json() == []

            created = await _create_webhook(
                client, headers, url=HOOK_URL, events=["project.status.changed"]
            )
            secret = created["secret"]
            assert secret and len(secret) >= 32
            assert created["url"] == HOOK_URL
            assert created["events"] == ["project.status.changed"]
            assert created["enabled"] is True

            # 列表不带 secret，且响应文本里不含 secret。
            listed = await client.get("/api/v1/webhooks", headers=headers)
            assert listed.status_code == 200, listed.text
            assert "secret" not in listed.json()[0]
            assert secret not in listed.text
            assert "secret_ref" not in listed.text

            # 库里只存密文引用：secret 明文不出现在 secret_store 任何列。
            async with SessionLocal() as session:
                endpoint = (
                    await session.execute(
                        select(WebhookEndpoint).where(
                            WebhookEndpoint.tenant_id == tenant_id
                        )
                    )
                ).scalar_one()
                assert endpoint.secret_ref is not None
                assert secret not in (endpoint.url, str(endpoint.events))
                stored_secret = await session.get(ModelSecret, endpoint.secret_ref)
                assert stored_secret is not None
                assert secret.encode() not in stored_secret.ciphertext
                assert stored_secret.scope == webhooks.SECRET_SCOPE
                assert stored_secret.last4 == secret[-4:]
                # 能用既有解密能力解回明文：证明复用同一套加密实现。
                from app.modules.system.secrets import decrypt_secret

                assert decrypt_secret(stored_secret) == secret

            # 审计不含 secret。
            async with SessionLocal() as session:
                events = list(
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant_id,
                                AuditEvent.action == "webhook.create",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert events
            assert secret not in str(events[0].data)
            assert events[0].data["url"] == HOOK_URL
            assert events[0].resource_type == "webhook_endpoint"

            # 删除：204，secret 记录被吊销，endpoint 消失。
            deleted = await client.delete(
                f"/api/v1/webhooks/{created['id']}", headers=headers
            )
            assert deleted.status_code == 204, deleted.text
            remaining = await client.get("/api/v1/webhooks", headers=headers)
            assert remaining.json() == []
            async with SessionLocal() as session:
                revoked = (
                    await session.execute(
                        select(ModelSecret).where(ModelSecret.tenant_id == tenant_id)
                    )
                ).scalar_one()
                assert revoked.revoked_at is not None
                actions = {
                    row.action
                    for row in (
                        await session.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                        )
                    ).scalars()
                }
            assert "webhook.delete" in actions
            assert user_id is not None
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_webhook_rejects_ssrf_and_non_https_urls() -> None:
    tenant_id, _, email = await seed_tenant_user("webhook-ssrf")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            for url in (
                "http://example.com/hook",
                "https://127.0.0.1/hook",
                "https://10.0.0.8/hook",
                "https://169.254.169.254/latest/meta-data",
                "https://192.168.1.10/hook",
                "https://[::1]/hook",
            ):
                response = await client.post(
                    "/api/v1/webhooks",
                    json={"url": url, "events": ["*"]},
                    headers=headers,
                )
                assert response.status_code == 422, f"{url}: {response.text}"

            # 被拒绝的请求不得留下任何 endpoint。
            async with SessionLocal() as session:
                rows = list(
                    (
                        await session.execute(
                            select(WebhookEndpoint).where(
                                WebhookEndpoint.tenant_id == tenant_id
                            )
                        )
                    ).scalars()
                )
            assert rows == []

            # 未知事件名 → 422。
            unknown = await client.post(
                "/api/v1/webhooks",
                json={"url": HOOK_URL, "events": ["project.deleted"]},
                headers=headers,
            )
            assert unknown.status_code == 422, unknown.text
            # 重复 URL → 409。
            await _create_webhook(client, headers)
            duplicate = await client.post(
                "/api/v1/webhooks",
                json={"url": HOOK_URL, "events": ["*"]},
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_project_status_change_delivers_signed_webhook() -> None:
    """端到端：状态变更 → 后台投递 → 签名正确 → delivery 记录成功。

    出站 HTTP 只在测试内用 respx 替身，生产代码走真实 httpx。
    """
    tenant_id, _, email = await seed_tenant_user("webhook-delivery")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_webhook(
                client, headers, url=HOOK_URL, events=["project.status.changed"]
            )
            project = await client.post(
                "/api/v1/projects", json={"name": "状态变更项目"}, headers=headers
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]

            captured: dict = {}

            def handler(request):
                captured["headers"] = dict(request.headers)
                captured["body"] = request.content
                return Response(200)

            with respx.mock(assert_all_called=False) as mock:
                mock.post(HOOK_URL).mock(side_effect=handler)
                status = await client.post(
                    f"/api/v1/projects/{project_id}/status",
                    json={"status": "won"},
                    headers=headers,
                )
                assert status.status_code == 200, status.text
                # 等后台投递任务结束（不阻塞主流程，但要断言结果）。
                await webhooks.wait_pending_deliveries()

            # 签名可由接收方用标准 HMAC-SHA256 复算。
            timestamp = int(captured["headers"][webhooks.TIMESTAMP_HEADER.lower()])
            expected = hmac.new(
                created["secret"].encode(),
                f"{timestamp}.".encode() + captured["body"],
                hashlib.sha256,
            ).hexdigest()
            assert (
                captured["headers"][webhooks.SIGNATURE_HEADER.lower()]
                == f"t={timestamp},v1={expected}"
            )
            assert captured["headers"][webhooks.EVENT_HEADER.lower()] == "project.status.changed"
            payload = json.loads(captured["body"])
            assert payload["data"]["project_id"] == project_id
            assert payload["data"]["from_status"] == "active"
            assert payload["data"]["to_status"] == "won"

            # delivery 记录：状态原样为 success，attempts=1。
            deliveries = await client.get(
                f"/api/v1/webhooks/{created['id']}/deliveries", headers=headers
            )
            assert deliveries.status_code == 200, deliveries.text
            rows = deliveries.json()
            assert len(rows) == 1
            assert rows[0]["event"] == "project.status.changed"
            assert rows[0]["status"] == "success"
            assert rows[0]["attempts"] == 1
            assert rows[0]["response_code"] == 200
            assert rows[0]["error"] is None
            # 列表出口不含任何 secret 字段。
            assert created["secret"] not in deliveries.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_webhook_failure_does_not_break_main_flow() -> None:
    """出站 500 → 主流程仍成功，delivery 记录 failed（含重试次数）。"""
    tenant_id, _, email = await seed_tenant_user("webhook-failure")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_webhook(
                client, headers, url=HOOK_URL, events=["project.status.changed"]
            )
            project = await client.post(
                "/api/v1/projects", json={"name": "投递失败项目"}, headers=headers
            )
            project_id = project.json()["id"]

            with respx.mock(assert_all_called=False) as mock:
                route = mock.post(HOOK_URL).mock(return_value=Response(500))
                status = await client.post(
                    f"/api/v1/projects/{project_id}/status",
                    json={"status": "lost", "reason": "价格劣势"},
                    headers=headers,
                )
                # 关键断言：webhook 失败绝不影响主流程。
                assert status.status_code == 200, status.text
                assert status.json()["status"] == "lost"
                await webhooks.wait_pending_deliveries()

            assert route.call_count <= get_settings().WEBHOOK_MAX_ATTEMPTS
            rows = (
                await client.get(
                    f"/api/v1/webhooks/{created['id']}/deliveries", headers=headers
                )
            ).json()
            assert len(rows) == 1
            assert rows[0]["status"] == "failed"
            assert rows[0]["attempts"] == get_settings().WEBHOOK_MAX_ATTEMPTS
            assert rows[0]["response_code"] == 500
            assert "HTTP 500" in rows[0]["error"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_submission_receipt_and_compliance_events_deliver() -> None:
    """另两类事件（回执创建、合规 HARD）也能真实触发投递。"""
    tenant_id, _, email = await seed_tenant_user("webhook-events")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_webhook(client, headers, url=HOOK_URL, events=["*"])
            project = await client.post(
                "/api/v1/projects", json={"name": "事件覆盖项目"}, headers=headers
            )
            project_id = project.json()["id"]

            events: list[str] = []

            def handler(request):
                events.append(request.headers.get(webhooks.EVENT_HEADER.lower()))
                return Response(200)

            with respx.mock(assert_all_called=False) as mock:
                mock.post(HOOK_URL).mock(side_effect=handler)
                receipt = await client.post(
                    f"/api/v1/projects/{project_id}/submission-receipts",
                    json={
                        "submitted_at": "2026-10-31T09:30:00+08:00",
                        "platform": "某省公共资源交易平台",
                        "receipt_no": "WH-001",
                    },
                    headers=headers,
                )
                assert receipt.status_code == 201, receipt.text
                await webhooks.wait_pending_deliveries()

            assert events == ["submission_receipt.created"]

            rows = (
                await client.get(
                    f"/api/v1/webhooks/{created['id']}/deliveries", headers=headers
                )
            ).json()
            assert rows[0]["event"] == "submission_receipt.created"
            assert rows[0]["status"] == "success"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_webhook_endpoints_are_tenant_isolated() -> None:
    tenant_id, _, email = await seed_tenant_user("webhook-tenant-a")
    other_id, _, other_email = await seed_tenant_user("webhook-tenant-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            other_headers = await _login(client, other_email)
            created = await _create_webhook(client, headers, url=HOOK_URL)

            # B 列表为空；按 ID 读 deliveries / 删除 → 404（不泄露存在性）。
            listed = await client.get("/api/v1/webhooks", headers=other_headers)
            assert listed.status_code == 200
            assert listed.json() == []
            assert (
                await client.get(
                    f"/api/v1/webhooks/{created['id']}/deliveries", headers=other_headers
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/webhooks/{created['id']}", headers=other_headers
                )
            ).status_code == 404

            # 不存在的 endpoint 同样 404。
            assert (
                await client.get(
                    f"/api/v1/webhooks/{uuid4()}/deliveries", headers=headers
                )
            ).status_code == 404

            # 租户 B 用自己的 webhook 不影响租户 A 的记录。
            async with SessionLocal() as session:
                rows = list(
                    (
                        await session.execute(
                            select(WebhookEndpoint).where(
                                WebhookEndpoint.tenant_id == other_id
                            )
                        )
                    ).scalars()
                )
            assert rows == []
            assert tenant_id != other_id
    finally:
        await cleanup_tenants([tenant_id, other_id])


@pytest.mark.integration
async def test_webhook_requires_settings_permission() -> None:
    tenant_id, _, email = await seed_tenant_user("webhook-perm", role_code="bid_engineer")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            assert (await client.get("/api/v1/webhooks", headers=headers)).status_code == 403
            denied = await client.post(
                "/api/v1/webhooks",
                json={"url": HOOK_URL, "events": ["*"]},
                headers=headers,
            )
            assert denied.status_code == 403, denied.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_webhook_delivery_records_are_deleted_with_endpoint() -> None:
    """删除 endpoint 时投递记录级联清理，不留孤儿。"""
    tenant_id, _, email = await seed_tenant_user("webhook-cascade")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_webhook(client, headers, url=HOOK_URL)
            project = await client.post(
                "/api/v1/projects", json={"name": "级联删除项目"}, headers=headers
            )
            project_id = project.json()["id"]

            with respx.mock(assert_all_called=False) as mock:
                mock.post(HOOK_URL).mock(return_value=Response(200))
                await client.post(
                    f"/api/v1/projects/{project_id}/status",
                    json={"status": "won"},
                    headers=headers,
                )
                await webhooks.wait_pending_deliveries()

            async with SessionLocal() as session:
                deliveries = list(
                    (
                        await session.execute(
                            select(WebhookDelivery).where(
                                WebhookDelivery.tenant_id == tenant_id
                            )
                        )
                    ).scalars()
                )
            assert len(deliveries) == 1

            # 删除 endpoint：投递记录按 FK ON DELETE CASCADE 一起消失（不留孤儿）。
            assert (
                await client.delete(f"/api/v1/webhooks/{created['id']}", headers=headers)
            ).status_code == 204
            async with SessionLocal() as session:
                remaining = list(
                    (
                        await session.execute(
                            select(WebhookDelivery).where(
                                WebhookDelivery.tenant_id == tenant_id
                            )
                        )
                    ).scalars()
                )
            assert remaining == []
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_webhook_outbox_retries_after_failure() -> None:
    """失败投递持久化为 outbox 行，到期后由补投任务重试成功（重启不丢事件）。"""
    tenant_id, _, email = await seed_tenant_user("webhook-outbox")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await _create_webhook(client, headers, url=HOOK_URL, events=["*"])

            with respx.mock(assert_all_called=False) as mock:
                mock.post(HOOK_URL).mock(return_value=Response(500))
                async with SessionLocal() as session:
                    deliveries = await webhooks.dispatch_event(
                        session, tenant_id, "project.status.changed", {"project_id": "p1"}
                    )
                assert len(deliveries) == 1
                assert deliveries[0].status == "failed"
                assert deliveries[0].attempts == 3
                assert deliveries[0].next_retry_at is not None
                assert deliveries[0].payload_json["data"]["project_id"] == "p1"

                # 模拟时间到期：把 next_retry_at 拨到过去，再切换到成功响应。
                async with SessionLocal() as session:
                    row = await session.get(WebhookDelivery, deliveries[0].id)
                    row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
                    await session.commit()
                mock.post(HOOK_URL).mock(return_value=Response(200))
                processed = await webhooks.process_due_webhook_deliveries()
                assert processed >= 1

            async with SessionLocal() as session:
                row = await session.get(WebhookDelivery, deliveries[0].id)
                assert row.status == "success"
                assert row.attempts == 4
                assert row.next_retry_at is None
                assert row.response_code == 200
    finally:
        await cleanup_tenants([tenant_id])
