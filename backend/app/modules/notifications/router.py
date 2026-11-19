from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.identity.deps import get_current_user
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications.schemas import NotificationReadRequest, NotificationResponse
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    service = NotificationService(session)
    items = await service.list_for_user(
        user.tenant_id, user.id, unread_only=unread_only, limit=limit
    )
    return {
        "items": [NotificationResponse.model_validate(item) for item in items],
        "unread": await service.unread_count(user.tenant_id, user.id),
    }


@router.get("/unread-count")
async def unread_count(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    return {
        "unread": await NotificationService(session).unread_count(user.tenant_id, user.id)
    }


@router.post("/read")
async def mark_read(
    body: NotificationReadRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    updated = await NotificationService(session).mark_read(
        user.tenant_id, user.id, ids=body.ids, mark_all=body.all
    )
    return {"updated": updated}
