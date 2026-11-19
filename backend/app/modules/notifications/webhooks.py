"""Webhook 出站：订阅管理、HMAC-SHA256 签名投递、投递留痕。

口径与既有能力对齐：
- **SSRF 防护**沿用 `app.modules.system.url_policy` 的判定（拒绝私网/回环/链路本地/
  组播/保留/未指定地址），但更严格：scheme 只允许 https，不接受 URL 内嵌账号密码。
- **secret 加密**复用 `app.modules.system.secrets.encrypt_secret` / `decrypt_secret`：
  明文经 AES-GCM 加密落 `secret_store`，`webhook_endpoints.secret_ref` 只存引用。
  secret 不进日志、不出现在列表接口。
- **失败不影响主流程**：`schedule_event` 只登记后台任务；投递异常全部在投递层吞掉
  并写 delivery 记录，绝不向调用方抛出。
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.session import SessionLocal
from app.modules.notifications.webhook_models import WebhookDelivery, WebhookEndpoint
from app.modules.system.models import ModelSecret
from app.modules.system.secrets import decrypt_secret, encrypt_secret, revoke_secret
from app.modules.system.url_policy import forbidden_outbound_address

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-BidCopilot-Signature"
TIMESTAMP_HEADER = "X-BidCopilot-Timestamp"
EVENT_HEADER = "X-BidCopilot-Event"
SIGNATURE_VERSION = "v1"

SECRET_SCOPE = "webhook"
SECRET_BYTES = 32

# 支持的事件：与挂载点一一对应，避免集成商订阅到永不触发的名字。
EVENTS = (
    "project.status.changed",
    "submission_receipt.created",
    "compliance.hard_finding",
)
WILDCARD_EVENT = "*"
MAX_EVENTS = 20
MAX_DELIVERY_ERROR_CHARS = 500
DELIVERY_LIST_LIMIT = 100

_pending_tasks: set[asyncio.Task[None]] = set()


def generate_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def _allowed_hosts() -> set[str]:
    settings = get_settings()
    return {
        host.strip().lower()
        for host in settings.WEBHOOK_ALLOWED_HOSTS.split(",")
        if host.strip()
    }


async def validate_webhook_url(url: str) -> str:
    """校验并归一化 webhook URL；不合法直接 422。

    只允许 https：webhook 会带租户业务数据出网，明文 http 一律拒绝。
    """
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationAppError("Webhook 地址必须是有效的 https 地址")
    if parsed.username or parsed.password:
        raise ValidationAppError("Webhook 地址不得包含用户名或密码")
    host = parsed.hostname.lower().rstrip(".")
    settings = get_settings()
    if settings.WEBHOOK_ALLOW_PRIVATE_URL or host in _allowed_hosts():
        return value
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError as exc:
            raise ValidationAppError("Webhook 地址无法解析") from exc
        addresses = {info[4][0] for info in infos}
        if not addresses or any(
            forbidden_outbound_address(str(address)) for address in addresses
        ):
            # from None：这里的失败与「不是 IP 字面量」的 ValueError 无因果关系。
            raise ValidationAppError("Webhook 地址不能指向内网、环回或链路本地地址") from None
        return value
    if forbidden_outbound_address(str(literal)):
        raise ValidationAppError("Webhook 地址不能指向内网、环回或链路本地地址")
    return value


def _normalize_events(events: list[str]) -> list[str]:
    cleaned: list[str] = []
    for event in events:
        value = (event or "").strip()
        if value and value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValidationAppError("events 不能为空")
    if len(cleaned) > MAX_EVENTS:
        raise ValidationAppError(f"events 最多 {MAX_EVENTS} 项")
    unknown = [value for value in cleaned if value not in EVENTS and value != WILDCARD_EVENT]
    if unknown:
        raise ValidationAppError(f"不支持的事件：{', '.join(sorted(unknown))}")
    return cleaned


def _canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sign_payload(secret: str, *, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256(secret, f"{timestamp}." + body)。

    时间戳纳入签名，接收方可以按容忍窗口拒绝重放；header 形如
    `t=1699999999,v1=<hex>`。
    """
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},{SIGNATURE_VERSION}={mac.hexdigest()}"


async def list_endpoints(session: AsyncSession, tenant_id: UUID) -> list[WebhookEndpoint]:
    return list(
        (
            await session.execute(
                select(WebhookEndpoint)
                .where(WebhookEndpoint.tenant_id == tenant_id)
                .order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_endpoint_or_404(
    session: AsyncSession, tenant_id: UUID, endpoint_id: UUID
) -> WebhookEndpoint:
    row = (
        await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.id == endpoint_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # 跨租户与不存在同口径，不泄露存在性。
        raise NotFoundError()
    return row


async def create_endpoint(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    url: str,
    events: list[str],
    secret: str | None = None,
) -> tuple[WebhookEndpoint, str]:
    """返回 (记录, 明文 secret)。明文仅此一次，调用方不得落库/写日志。"""
    value = await validate_webhook_url(url)
    normalized = _normalize_events(events)
    existing = (
        await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.url == value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Webhook 地址已存在")
    plaintext = (secret or "").strip() or generate_secret()
    if len(plaintext) < 16:
        raise ValidationAppError("Webhook secret 长度至少 16 位")
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url=value,
        events=normalized,
        enabled=True,
        created_by=user_id,
    )
    session.add(endpoint)
    await session.flush()
    encrypted = encrypt_secret(
        tenant_id=tenant_id,
        scope=SECRET_SCOPE,
        # secret_store.owner_id 的外键指向 users，不能放 endpoint id；
        # 用 None 让 AAD 只绑定租户，保持稳定（用户被删/轮换都不会解不开）。
        # 每条记录的非随机 nonce + AES-GCM tag 仍保证单条完整性。
        owner_id=None,
        provider="webhook",
        plaintext=plaintext,
        created_by=user_id,
    )
    session.add(encrypted)
    await session.flush()
    endpoint.secret_ref = encrypted.id
    await session.commit()
    await session.refresh(endpoint)
    return endpoint, plaintext


async def delete_endpoint(
    session: AsyncSession, tenant_id: UUID, endpoint_id: UUID
) -> None:
    endpoint = await get_endpoint_or_404(session, tenant_id, endpoint_id)
    if endpoint.secret_ref is not None:
        secret = await session.get(ModelSecret, endpoint.secret_ref)
        if secret is not None:
            revoke_secret(secret)
    await session.delete(endpoint)
    await session.commit()


async def list_deliveries(
    session: AsyncSession,
    tenant_id: UUID,
    endpoint_id: UUID,
    *,
    limit: int = DELIVERY_LIST_LIMIT,
) -> list[WebhookDelivery]:
    await get_endpoint_or_404(session, tenant_id, endpoint_id)
    return list(
        (
            await session.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.tenant_id == tenant_id,
                    WebhookDelivery.endpoint_id == endpoint_id,
                )
                .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
                .limit(max(1, min(limit, DELIVERY_LIST_LIMIT)))
            )
        )
        .scalars()
        .all()
    )


async def _endpoint_secret(session: AsyncSession, endpoint: WebhookEndpoint) -> str | None:
    if endpoint.secret_ref is None:
        return None
    secret = await session.get(ModelSecret, endpoint.secret_ref)
    return decrypt_secret(secret)


def _subscribes(endpoint: WebhookEndpoint, event: str) -> bool:
    events = list(endpoint.events or [])
    return WILDCARD_EVENT in events or event in events


MAX_TOTAL_ATTEMPTS = 10
# 新建 pending 行的崩溃恢复宽限：避免调度器与在线投递对同一事件重复发送。
PENDING_GRACE_SECONDS = 120


def _retry_delay_seconds(attempts: int) -> int:
    return min(60 * (2 ** max(attempts - 1, 0)), 3600)


async def _run_attempts(
    session: AsyncSession,
    endpoint: WebhookEndpoint,
    delivery: WebhookDelivery,
    *,
    secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebhookDelivery:
    settings = get_settings()
    body = _canonical_body(delivery.payload_json or {})
    start = delivery.attempts
    stop = min(start + settings.WEBHOOK_MAX_ATTEMPTS, MAX_TOTAL_ATTEMPTS)
    delivered = False
    response_code = delivery.response_code
    error = delivery.error
    for attempt in range(start + 1, stop + 1):
        delivery.attempts = attempt
        timestamp = int(datetime.now(UTC).timestamp())
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: delivery.event,
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign_payload(secret, timestamp=timestamp, body=body),
            "User-Agent": "BidCopilot-Webhook/1.0",
        }
        try:
            # 每次投递前重新校验：创建后 DNS 可能改指内网（rebinding 防御）。
            await validate_webhook_url(endpoint.url)
            async with httpx.AsyncClient(
                timeout=settings.WEBHOOK_TIMEOUT_SECONDS, transport=transport, trust_env=False
            ) as client:
                response = await client.post(endpoint.url, content=body, headers=headers)
            response_code = response.status_code
            if 200 <= response_code < 300:
                error = None
                delivered = True
                break
            error = f"HTTP {response_code}"
        except Exception as exc:  # 网络异常一律降级为失败记录
            # 只记异常类型与消息；secret 不在异常信息里。
            error = f"{type(exc).__name__}: {exc}"
            response_code = None
        if attempt < stop and settings.WEBHOOK_RETRY_BACKOFF_SECONDS:
            await asyncio.sleep(
                settings.WEBHOOK_RETRY_BACKOFF_SECONDS * (2 ** (attempt - start - 1))
            )

    delivery.response_code = response_code
    if delivered:
        delivery.status = "success"
        delivery.error = None
        delivery.next_retry_at = None
    else:
        delivery.status = "failed"
        delivery.error = (error or "unknown error")[:MAX_DELIVERY_ERROR_CHARS]
        delivery.next_retry_at = (
            datetime.now(UTC) + timedelta(seconds=_retry_delay_seconds(delivery.attempts))
            if delivery.attempts < MAX_TOTAL_ATTEMPTS
            else None
        )
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def deliver_to_endpoint(
    session: AsyncSession,
    endpoint: WebhookEndpoint,
    *,
    event: str,
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebhookDelivery:
    """投递一次事件（含有限重试），结果写 delivery 记录。

    `transport` 仅供测试注入替身；生产路径传 None 走真实网络。
    先落 pending outbox 行再投递：进程崩溃后由调度器补投，不丢事件。
    """
    delivery = WebhookDelivery(
        tenant_id=endpoint.tenant_id,
        endpoint_id=endpoint.id,
        event=event,
        status="pending",
        attempts=0,
        response_code=None,
        error=None,
        payload_json=payload,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=PENDING_GRACE_SECONDS),
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    try:
        secret = await _endpoint_secret(session, endpoint)
    except Exception:
        secret = None
    if not secret:
        delivery.status = "failed"
        delivery.error = "webhook secret 不可用，拒绝投递"
        delivery.next_retry_at = None
        await session.commit()
        await session.refresh(delivery)
        return delivery
    return await _run_attempts(session, endpoint, delivery, secret=secret, transport=transport)


async def process_due_webhook_deliveries(limit: int = 50) -> int:
    """调度器补投：处理到期未完成/可重试的 outbox 行，返回处理条数。"""
    now = datetime.now(UTC)
    processed = 0
    async with SessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.next_retry_at.is_not(None),
                        WebhookDelivery.next_retry_at <= now,
                        WebhookDelivery.attempts < MAX_TOTAL_ATTEMPTS,
                    )
                    .order_by(WebhookDelivery.next_retry_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for delivery in rows:
            endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
            if endpoint is None or not endpoint.enabled:
                delivery.next_retry_at = None
                continue
            try:
                secret = await _endpoint_secret(session, endpoint)
            except Exception:
                secret = None
            if not secret:
                delivery.status = "failed"
                delivery.error = "webhook secret 不可用，拒绝投递"
                delivery.next_retry_at = None
                continue
            await _run_attempts(session, endpoint, delivery, secret=secret)
            processed += 1
        await session.commit()
    return processed


def render_event_payload(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "occurred_at": datetime.now(UTC).isoformat(),
        "data": data,
    }


async def dispatch_event(
    session: AsyncSession,
    tenant_id: UUID,
    event: str,
    data: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[WebhookDelivery]:
    """向本租户所有订阅该事件的 endpoint 投递；单个 endpoint 失败不影响其他。"""
    endpoints = list(
        (
            await session.execute(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.tenant_id == tenant_id,
                    WebhookEndpoint.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    payload = render_event_payload(event, data)
    deliveries: list[WebhookDelivery] = []
    for endpoint in endpoints:
        if not _subscribes(endpoint, event):
            continue
        try:
            deliveries.append(
                await deliver_to_endpoint(
                    session, endpoint, event=event, payload=payload, transport=transport
                )
            )
        except Exception:
            # 投递层任何异常都不得冒泡到业务主流程。
            logger.warning(
                "webhook delivery failed event=%s tenant_id=%s endpoint_id=%s",
                event,
                tenant_id,
                endpoint.id,
                exc_info=True,
            )
            await session.rollback()
    return deliveries


async def _run_background(tenant_id: UUID, event: str, data: dict[str, Any]) -> None:
    try:
        async with SessionLocal() as session:
            await dispatch_event(session, tenant_id, event, data)
    except Exception:
        logger.warning(
            "webhook background dispatch failed event=%s tenant_id=%s",
            event,
            tenant_id,
            exc_info=True,
        )


def schedule_event(tenant_id: UUID, event: str, data: dict[str, Any]) -> None:
    """登记后台投递任务，立即返回。

    用独立 session 执行，避免与请求事务耦合；webhook 失败不影响主流程。
    没有运行中的事件循环（例如脚本/同步上下文）时静默跳过。
    """
    if not get_settings().WEBHOOK_DELIVERY_ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run_background(tenant_id, event, data))
    # 持有引用，避免任务被 GC 提前回收。
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def wait_pending_deliveries() -> None:
    """等待已登记的后台投递结束（lifespan 收尾与测试用）。"""
    while _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


def notify_project_status_changed(
    tenant_id: UUID,
    *,
    project_id: UUID,
    from_status: str,
    to_status: str,
    reason: str | None,
) -> None:
    """项目状态变更事件（在状态流转落库成功后调用）。"""
    schedule_event(
        tenant_id,
        "project.status.changed",
        {
            "project_id": str(project_id),
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
        },
    )


def notify_submission_receipt_created(
    tenant_id: UUID,
    *,
    project_id: UUID,
    receipt_id: UUID,
    platform: str,
    receipt_no: str | None,
    submitted_at: str | None,
) -> None:
    """交标回执创建事件（在回执落库成功后调用）。"""
    schedule_event(
        tenant_id,
        "submission_receipt.created",
        {
            "project_id": str(project_id),
            "receipt_id": str(receipt_id),
            "platform": platform,
            "receipt_no": receipt_no,
            "submitted_at": submitted_at,
        },
    )


def notify_compliance_hard_findings(
    tenant_id: UUID,
    *,
    project_id: UUID,
    report_id: UUID,
    hard_count: int,
    rule_ids: list[str],
) -> None:
    """合规 HARD 问题事件；hard_count 为 0 时不发（避免噪声）。"""
    if hard_count <= 0:
        return
    schedule_event(
        tenant_id,
        "compliance.hard_finding",
        {
            "project_id": str(project_id),
            "report_id": str(report_id),
            "hard_count": hard_count,
            "rule_ids": rule_ids,
        },
    )
