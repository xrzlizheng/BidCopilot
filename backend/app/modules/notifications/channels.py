import asyncio
import logging
import smtplib
from email.message import EmailMessage
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ConflictError, ValidationAppError
from app.modules.notifications.models import NotificationChannel
from app.modules.notifications.webhooks import validate_webhook_url

logger = logging.getLogger(__name__)

KINDS = {"wechat", "email"}


async def list_channels(session: AsyncSession, tenant_id: UUID) -> list[NotificationChannel]:
    return list(
        (
            await session.execute(
                select(NotificationChannel)
                .where(NotificationChannel.tenant_id == tenant_id)
                .order_by(NotificationChannel.created_at)
            )
        )
        .scalars()
        .all()
    )


async def create_channel(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    kind: str,
    target: str,
    label: str | None,
    user_id: UUID,
) -> NotificationChannel:
    if kind not in KINDS:
        raise ValidationAppError("kind must be wechat or email")
    value = target.strip()
    if not value:
        raise ValidationAppError("target 不能为空")
    if kind == "wechat":
        # 与开放 API Webhook 同一入站校验：https + 禁私网/环回/链路本地（防 SSRF）。
        value = await validate_webhook_url(value)
    if kind == "email":
        emails = [item.strip() for item in value.split(",") if item.strip()]
        if not emails or any("@" not in item for item in emails):
            raise ValidationAppError("邮件目标必须是逗号分隔的邮箱地址")
    existing = (
        await session.execute(
            select(NotificationChannel).where(
                NotificationChannel.tenant_id == tenant_id,
                NotificationChannel.kind == kind,
                NotificationChannel.target == value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("通知渠道已存在")
    channel = NotificationChannel(
        tenant_id=tenant_id,
        kind=kind,
        target=value,
        label=label,
        created_by=user_id,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return channel


async def delete_channel(session: AsyncSession, tenant_id: UUID, channel_id: UUID) -> None:
    channel = (
        await session.execute(
            select(NotificationChannel).where(
                NotificationChannel.tenant_id == tenant_id,
                NotificationChannel.id == channel_id,
            )
        )
    ).scalar_one_or_none()
    if channel is None:
        return
    await session.delete(channel)
    await session.commit()


def _wechat_payload(title: str, body: str | None) -> dict:
    return {
        "msgtype": "text",
        "text": {"content": f"{title}\n{body}" if body else title},
    }


def _send_email(target: str, title: str, body: str | None) -> None:
    settings = get_settings()
    if not (settings.SMTP_HOST and settings.SMTP_FROM):
        logger.info("SMTP not configured; skip email channel")
        return
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = settings.SMTP_FROM
    message["To"] = target
    message.set_content(body or title)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def _send_wechat(target: str, title: str, body: str | None) -> None:
    # 投递前重新校验：创建后 DNS 可能改指向内网（rebinding 防御的第一道）。
    await validate_webhook_url(target)
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.post(target, json=_wechat_payload(title, body))
        response.raise_for_status()


async def dispatch_channels(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    title: str,
    body: str | None,
) -> None:
    channels = await list_channels(session, tenant_id)
    for channel in channels:
        if not channel.enabled:
            continue
        try:
            if channel.kind == "wechat":
                await _send_wechat(channel.target, title, body)
            elif channel.kind == "email":
                await asyncio.to_thread(_send_email, channel.target, title, body)
        except Exception:
            logger.warning("notification channel delivery failed kind=%s", channel.kind, exc_info=True)
