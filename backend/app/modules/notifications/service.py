from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.channels import dispatch_channels
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import NotificationRepository


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def notify(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        notification_type: str,
        title: str,
        body: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> Notification:
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            type=notification_type,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        self.session.add(notification)
        await self.session.commit()
        await self.session.refresh(notification)
        await dispatch_channels(self.session, tenant_id, title=title, body=body)
        return notification

    async def list_for_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[Notification]:
        statement = NotificationRepository(self.session, tenant_id).scoped(
            select(Notification)
        ).where(Notification.user_id == user_id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        rows = (
            await self.session.execute(
                statement.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
            )
        ).scalars().all()
        return list(rows)

    async def unread_count(self, tenant_id: UUID, user_id: UUID) -> int:
        statement = (
            NotificationRepository(self.session, tenant_id)
            .scoped(select(func.count()).select_from(Notification))
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        return int((await self.session.execute(statement)).scalar_one())

    async def mark_read(
        self,
        tenant_id: UUID,
        user_id: UUID,
        *,
        ids: list[UUID] | None = None,
        mark_all: bool = False,
    ) -> int:
        if not mark_all and not ids:
            return 0
        statement = (
            update(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        if not mark_all:
            statement = statement.where(Notification.id.in_(ids or []))
        result = await self.session.execute(statement)
        await self.session.commit()
        # DML 语句的 execute 实际返回 CursorResult（带 rowcount）；AsyncSession.execute
        # 的签名只标注了 Result，这里按真实类型收敛，避免把 rowcount 丢成 getattr 猜测。
        return int(cast(CursorResult[Any], result).rowcount or 0)
