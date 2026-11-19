from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.access.service import AuthzService
from app.modules.audit.service import AuditService
from app.modules.compliance.access_service import (
    approve_request,
    create_request,
    deny_request,
    list_requests,
)
from app.modules.compliance.schemas import AccessRequestCreate, AccessRequestResponse
from app.modules.identity.deps import get_current_user, require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1/compliance/access-requests", tags=["compliance"])


@router.get("", response_model=list[AccessRequestResponse])
async def get_access_requests(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list:
    can_review = AuthzService.can_capability(user, "compliance:review").allow
    return await list_requests(session, user=user, can_review=can_review)


@router.post("", response_model=AccessRequestResponse, status_code=201)
async def post_access_request(
    body: AccessRequestCreate,
    user: CurrentUser = Depends(require_permission("compliance:break_glass")),
    session: AsyncSession = Depends(get_session),
) -> object:
    row = await create_request(
        session,
        user=user,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        reason=body.reason,
        requested_minutes=body.requested_minutes,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.break_glass.request",
        resource_type=body.resource_type,
        resource_id=str(body.resource_id),
        data={"request_id": str(row.id), "minutes": row.requested_minutes},
    )
    await session.commit()
    return row


@router.post("/{request_id}/approve", response_model=AccessRequestResponse)
async def post_approve_request(
    request_id: UUID,
    user: CurrentUser = Depends(require_permission("compliance:review")),
    session: AsyncSession = Depends(get_session),
) -> object:
    row = await approve_request(session, user=user, request_id=request_id)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.break_glass.approve",
        resource_type=row.resource_type,
        resource_id=str(row.resource_id),
        data={"request_id": str(row.id), "expires_at": row.expires_at.isoformat() if row.expires_at else None},
    )
    await session.commit()
    return row


@router.post("/{request_id}/deny", response_model=AccessRequestResponse)
async def post_deny_request(
    request_id: UUID,
    user: CurrentUser = Depends(require_permission("compliance:review")),
    session: AsyncSession = Depends(get_session),
) -> object:
    row = await deny_request(session, user=user, request_id=request_id)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.break_glass.deny",
        resource_type=row.resource_type,
        resource_id=str(row.resource_id),
        data={"request_id": str(row.id)},
    )
    await session.commit()
    return row
