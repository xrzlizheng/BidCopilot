from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.compliance.models import ComplianceAccessRequest
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.models import KnowledgeBase, KnowledgePack

RESOURCE_TYPES = {"knowledge_base", "knowledge_pack"}


async def create_request(
    session: AsyncSession,
    *,
    user: CurrentUser,
    resource_type: str,
    resource_id: UUID,
    reason: str,
    requested_minutes: int,
) -> ComplianceAccessRequest:
    if resource_type not in RESOURCE_TYPES:
        raise ValidationAppError("仅支持知识库/模板调阅")
    if requested_minutes < 1 or requested_minutes > get_settings().BREAK_GLASS_MAX_MINUTES:
        raise ValidationAppError(
            f"调阅时长必须在 1-{get_settings().BREAK_GLASS_MAX_MINUTES} 分钟内"
        )
    if resource_type == "knowledge_base":
        resource: KnowledgeBase | KnowledgePack | None = (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == user.tenant_id,
                    KnowledgeBase.id == resource_id,
                )
            )
        ).scalar_one_or_none()
    else:
        resource = (
            await session.execute(
                select(KnowledgePack).where(
                    KnowledgePack.tenant_id == user.tenant_id,
                    KnowledgePack.id == resource_id,
                )
            )
        ).scalar_one_or_none()
    if resource is None:
        raise NotFoundError()
    if resource.scope != "personal":
        raise ValidationAppError("只有个人资源需要 break-glass 调阅")
    if resource.owner_id == user.id:
        raise ValidationAppError("资源所有者无需申请调阅")
    row = ComplianceAccessRequest(
        tenant_id=user.tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        requester_id=user.id,
        reason=reason.strip(),
        status="pending",
        requested_minutes=requested_minutes,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _request_or_404(
    session: AsyncSession, tenant_id: UUID, request_id: UUID
) -> ComplianceAccessRequest:
    row = (
        await session.execute(
            select(ComplianceAccessRequest).where(
                ComplianceAccessRequest.tenant_id == tenant_id,
                ComplianceAccessRequest.id == request_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    return row


async def approve_request(
    session: AsyncSession, *, user: CurrentUser, request_id: UUID
) -> ComplianceAccessRequest:
    row = await _request_or_404(session, user.tenant_id, request_id)
    if row.status != "pending":
        raise ValidationAppError("调阅申请已处理")
    if row.requester_id == user.id:
        raise ForbiddenError("审批人不能是申请人")
    now = datetime.now(UTC)
    row.status = "approved"
    row.approver_id = user.id
    row.decided_at = now
    row.expires_at = now + timedelta(minutes=row.requested_minutes)
    await session.commit()
    await session.refresh(row)
    return row


async def deny_request(
    session: AsyncSession, *, user: CurrentUser, request_id: UUID, reason: str | None = None
) -> ComplianceAccessRequest:
    row = await _request_or_404(session, user.tenant_id, request_id)
    if row.status != "pending":
        raise ValidationAppError("调阅申请已处理")
    if row.requester_id == user.id:
        raise ForbiddenError("审批人不能是申请人")
    row.status = "denied"
    row.approver_id = user.id
    row.decided_at = datetime.now(UTC)
    if reason:
        row.reason = f"{row.reason}\n拒绝理由：{reason.strip()}"
    await session.commit()
    await session.refresh(row)
    return row


async def list_requests(
    session: AsyncSession, *, user: CurrentUser, can_review: bool
) -> list[ComplianceAccessRequest]:
    statement = select(ComplianceAccessRequest).where(
        ComplianceAccessRequest.tenant_id == user.tenant_id
    )
    if not can_review:
        statement = statement.where(ComplianceAccessRequest.requester_id == user.id)
    return list(
        (
            await session.execute(
                statement.order_by(ComplianceAccessRequest.created_at.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )


async def has_active_break_glass(
    session: AsyncSession,
    *,
    user: CurrentUser,
    resource_type: str,
    resource_id: UUID,
) -> bool:
    row = (
        await session.execute(
            select(ComplianceAccessRequest.id)
            .where(
                ComplianceAccessRequest.tenant_id == user.tenant_id,
                ComplianceAccessRequest.resource_type == resource_type,
                ComplianceAccessRequest.resource_id == resource_id,
                ComplianceAccessRequest.requester_id == user.id,
                ComplianceAccessRequest.status == "approved",
                ComplianceAccessRequest.expires_at > datetime.now(UTC),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def active_break_glass_resource_ids(
    session: AsyncSession, *, user: CurrentUser, resource_type: str
) -> list[UUID]:
    return list(
        (
            await session.execute(
                select(ComplianceAccessRequest.resource_id).where(
                    ComplianceAccessRequest.tenant_id == user.tenant_id,
                    ComplianceAccessRequest.resource_type == resource_type,
                    ComplianceAccessRequest.requester_id == user.id,
                    ComplianceAccessRequest.status == "approved",
                    ComplianceAccessRequest.expires_at > datetime.now(UTC),
                )
            )
        )
        .scalars()
        .all()
    )
