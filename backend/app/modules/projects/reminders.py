import logging
import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import TenantRepository
from app.modules.notifications.service import NotificationService
from app.modules.projects.models import BidProject, ProjectReadinessItem, ProjectReminder
from app.modules.projects.service import get_latest_constraint
from app.modules.system.models import TenantSetting

logger = logging.getLogger("bidcopilot.reminders")
CST = timezone(timedelta(hours=8))
# 显式标注 dict[str, Any]：不标注会推断成 dict[str, object]，
# 后续 list(...)/int(...) 收敛 object 值会触发 mypy call-overload。
DEFAULT_SETTINGS: dict[str, Any] = {"enabled": True, "offsets": [7, 3, 1], "buffer_days": 2}
NODE_LABELS = {
    "bid_deadline": "投标截止",
    "internal_deadline": "内部递交截止",
    "qa_deadline": "答疑截止",
    "deposit_deadline": "保证金截止",
    "opening": "开标",
}
NODE_KEYWORDS = {
    "qa_deadline": ("答疑", "澄清"),
    "deposit_deadline": ("保证金",),
    "opening": ("开标",),
}
DATE_RE = re.compile(r"(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})")


class ReminderRepository(TenantRepository[ProjectReminder]):
    model = ProjectReminder


async def get_reminder_settings(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "reminder_settings",
            )
        )
    ).scalar_one_or_none()
    value: dict[str, Any] = dict(DEFAULT_SETTINGS)
    if row is not None and isinstance(row.value, dict):
        value.update(row.value)
    offsets = value.get("offsets")
    if not isinstance(offsets, list):
        offsets = list(DEFAULT_SETTINGS["offsets"])
    value["offsets"] = sorted(
        {
            int(offset)
            for offset in offsets
            if isinstance(offset, (int, float, str)) and str(offset).isdigit() and 0 < int(offset) <= 60
        },
        reverse=True,
    ) or list(DEFAULT_SETTINGS["offsets"])
    try:
        value["buffer_days"] = max(0, min(30, int(value.get("buffer_days", 2))))
    except (TypeError, ValueError):
        value["buffer_days"] = 2
    value["enabled"] = bool(value.get("enabled", True))
    return value


def _parse_node_time(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("deadline_at")
    if raw:
        try:
            value = datetime.fromisoformat(str(raw))
            return value if value.tzinfo else value.replace(tzinfo=CST)
        except ValueError:
            pass
    match = DATE_RE.search(str(entry.get("at") or ""))
    if match is None:
        return None
    try:
        stamp = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return datetime.combine(stamp, time(9, 0), tzinfo=CST)


async def resolve_project_nodes(
    session: AsyncSession,
    project: BidProject,
    *,
    buffer_days: int,
) -> dict[str, datetime]:
    nodes: dict[str, datetime] = {}
    if project.bid_deadline is not None:
        nodes["bid_deadline"] = project.bid_deadline
    if project.internal_deadline is not None:
        nodes["internal_deadline"] = project.internal_deadline
    elif project.bid_deadline is not None:
        nodes["internal_deadline"] = project.bid_deadline - timedelta(days=buffer_days)
    constraint = await get_latest_constraint(session, project.tenant_id, project.id)
    timeline = (constraint.payload or {}).get("timeline") if constraint else []
    for node, keywords in NODE_KEYWORDS.items():
        for entry in timeline or []:
            if not isinstance(entry, dict):
                continue
            event = str(entry.get("event") or "")
            if not any(keyword in event for keyword in keywords):
                continue
            value = _parse_node_time(entry)
            if value is not None:
                nodes[node] = value
                break
    return nodes


async def _recipients(
    session: AsyncSession, tenant_id: UUID, project: BidProject
) -> list[UUID]:
    recipients: list[UUID] = []
    if project.owner_id is not None:
        recipients.append(project.owner_id)
    items = list(
        (
            await session.execute(
                select(ProjectReadinessItem).where(
                    ProjectReadinessItem.tenant_id == tenant_id,
                    ProjectReadinessItem.project_id == project.id,
                )
            )
        ).scalars()
    )
    for item in items:
        if item.owner_id is not None:
            recipients.append(item.owner_id)
        for value in item.collaborator_ids or []:
            try:
                recipients.append(UUID(str(value)))
            except (ValueError, TypeError):
                continue
    return list(dict.fromkeys(recipients))[:10]


async def _send_reminder(
    session: AsyncSession,
    project: BidProject,
    node: str,
    offset_days: int,
    due_at: datetime,
    recipients: list[UUID],
) -> None:
    service = NotificationService(session)
    label = NODE_LABELS.get(node, node)
    title = f"{label}还有 {offset_days} 天：{project.name}"
    body = (
        f"项目「{project.name}」的{label}为 {due_at.astimezone(CST).strftime('%Y-%m-%d %H:%M')}，"
        f"请检查交标自查清单中的阻断项与责任人。"
    )
    for user_id in recipients:
        await service.notify(
            tenant_id=project.tenant_id,
            user_id=user_id,
            notification_type=f"project_reminder:{node}",
            title=title,
            body=body,
            resource_type="bid_project",
            resource_id=str(project.id),
        )


async def scan_due_project_reminders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    tenant_id: UUID | None = None,
) -> int:
    now = now or datetime.now(UTC)
    statement = select(BidProject).where(
        BidProject.status == "active",
        BidProject.owner_id.is_not(None),
        (BidProject.bid_deadline.is_not(None) | BidProject.internal_deadline.is_not(None)),
    )
    if tenant_id is not None:
        statement = statement.where(BidProject.tenant_id == tenant_id)
    projects = list((await session.execute(statement.limit(500))).scalars())
    settings_by_tenant: dict[UUID, dict[str, Any]] = {}
    sent = 0
    for project in projects:
        # session.rollback() 会 expire 全部 ORM 实例，失败分支里再访问 project.* 会
        # 触发隐式 IO（async 下表现为 MissingGreenlet），故在此固定为局部变量。
        project_tenant_id = project.tenant_id
        project_id = project.id
        settings = settings_by_tenant.get(project_tenant_id)
        if settings is None:
            settings = await get_reminder_settings(session, project_tenant_id)
            settings_by_tenant[project_tenant_id] = settings
        if not settings["enabled"]:
            continue
        nodes = await resolve_project_nodes(
            session, project, buffer_days=int(settings["buffer_days"])
        )
        recipients = await _recipients(session, project_tenant_id, project)
        if not recipients:
            continue
        repo = ReminderRepository(session, project_tenant_id)
        for node, due_at in nodes.items():
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=CST)
            for offset in settings["offsets"]:
                scheduled_at = due_at - timedelta(days=offset)
                if now < scheduled_at or now > due_at:
                    continue
                existing = (
                    await session.execute(
                        repo.scoped(
                            select(ProjectReminder).where(
                                ProjectReminder.project_id == project_id,
                                ProjectReminder.node == node,
                                ProjectReminder.offset_days == offset,
                            )
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.status == "sent":
                    continue
                if existing is not None and existing.attempts >= 3:
                    continue
                if existing is not None and existing.next_retry_at and existing.next_retry_at > now:
                    continue
                if existing is None:
                    existing = ProjectReminder(
                        tenant_id=project_tenant_id,
                        project_id=project_id,
                        node=node,
                        offset_days=offset,
                        scheduled_at=scheduled_at,
                        status="pending",
                        dedup_key=f"{project_id}:{node}:{offset}",
                    )
                    session.add(existing)
                    await session.flush()
                dedup_key = existing.dedup_key
                existing.attempts = (existing.attempts or 0) + 1
                try:
                    await _send_reminder(
                        session, project, node, offset, due_at, recipients
                    )
                    existing.status = "sent"
                    existing.sent_at = now
                    existing.last_error = None
                    existing.next_retry_at = None
                    await session.commit()
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    row = (
                        await session.execute(
                            select(ProjectReminder).where(
                                ProjectReminder.dedup_key == dedup_key,
                                ProjectReminder.tenant_id == project_tenant_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        row.status = "failed"
                        row.attempts = max(row.attempts or 0, 1)
                        row.last_error = str(exc)[:500]
                        row.next_retry_at = now + timedelta(hours=1)
                        await session.commit()
                    else:
                        row = ProjectReminder(
                            tenant_id=project_tenant_id,
                            project_id=project_id,
                            node=node,
                            offset_days=offset,
                            scheduled_at=scheduled_at,
                            status="failed",
                            dedup_key=dedup_key,
                            attempts=1,
                            last_error=str(exc)[:500],
                            next_retry_at=now + timedelta(hours=1),
                        )
                        session.add(row)
                        await session.commit()
                    logger.exception("project reminder failed: %s", dedup_key)
    return sent
