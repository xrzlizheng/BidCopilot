from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.models import AuditEvent
from app.modules.audit.schemas import AuditEventResponse
from app.modules.audit.service import AuditService
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/events")
async def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = None,
    resource_type: str | None = None,
    user: CurrentUser = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    base = select(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id)
    count_statement = (
        select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id)
    )
    if action:
        base = base.where(AuditEvent.action == action)
        count_statement = count_statement.where(AuditEvent.action == action)
    if resource_type:
        base = base.where(AuditEvent.resource_type == resource_type)
        count_statement = count_statement.where(AuditEvent.resource_type == resource_type)
    total = (await session.execute(count_statement)).scalar_one()
    events = (
        (
            await session.execute(
                base.order_by(AuditEvent.seq.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [AuditEventResponse.model_validate(event) for event in events],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/verify")
async def verify_events(
    user: CurrentUser = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    events = (
        await session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.tenant_id == user.tenant_id)
        )
    ).scalar_one()
    valid = await AuditService(session).verify_chain(user.tenant_id)
    return {"valid": valid, "events": events}
