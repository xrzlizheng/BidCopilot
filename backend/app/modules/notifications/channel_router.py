from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications import channels
from app.modules.notifications.channel_schemas import ChannelCreate, ChannelResponse

router = APIRouter(prefix="/api/v1/notifications/channels", tags=["notifications"])


@router.get("", response_model=list[ChannelResponse])
async def list_channels(
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[ChannelResponse]:
    rows = await channels.list_channels(session, user.tenant_id)
    return [ChannelResponse.model_validate(row) for row in rows]


@router.post("", response_model=ChannelResponse, status_code=201)
async def create_channel(
    body: ChannelCreate,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> ChannelResponse:
    row = await channels.create_channel(
        session,
        tenant_id=user.tenant_id,
        kind=body.kind,
        target=body.target,
        label=body.label,
        user_id=user.id,
    )
    return ChannelResponse.model_validate(row)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: UUID,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await channels.delete_channel(session, user.tenant_id, channel_id)
