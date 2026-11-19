from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications import webhooks

router = APIRouter(prefix="/api/v1", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    events: list[str] = Field(min_length=1, max_length=webhooks.MAX_EVENTS)
    # 不传则服务端生成；自定义 secret 仍只加密入库、仅本响应返回一次。
    secret: str | None = Field(default=None, min_length=16, max_length=256)


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    events: list[str]
    enabled: bool
    created_by: UUID | None
    created_at: datetime


class WebhookCreated(WebhookResponse):
    # 与 secret 同理：明文只创建时返回一次，列表/详情永不带。
    secret: str


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    event: str
    status: str
    attempts: int
    response_code: int | None
    error: str | None
    created_at: datetime


async def _audit(
    session: AsyncSession, user: CurrentUser, action: str, resource_id: str, data: dict
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type="webhook_endpoint",
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.get("/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookResponse]:
    rows = await webhooks.list_endpoints(session, user.tenant_id)
    return [WebhookResponse.model_validate(row) for row in rows]


@router.post("/webhooks", response_model=WebhookCreated, status_code=201)
async def create_webhook(
    body: WebhookCreate,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> WebhookCreated:
    row, secret = await webhooks.create_endpoint(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        url=body.url,
        events=body.events,
        secret=body.secret,
    )
    # 审计只记 url/events，绝不含 secret。
    await _audit(
        session,
        user,
        "webhook.create",
        str(row.id),
        {"url": row.url, "events": list(row.events)},
    )
    return WebhookCreated(**WebhookResponse.model_validate(row).model_dump(), secret=secret)


@router.delete("/webhooks/{endpoint_id}", status_code=204)
async def delete_webhook(
    endpoint_id: UUID,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await webhooks.get_endpoint_or_404(session, user.tenant_id, endpoint_id)
    url = row.url
    await webhooks.delete_endpoint(session, user.tenant_id, endpoint_id)
    await _audit(
        session,
        user,
        "webhook.delete",
        str(endpoint_id),
        {"url": url},
    )


@router.get(
    "/webhooks/{endpoint_id}/deliveries",
    response_model=list[WebhookDeliveryResponse],
)
async def list_webhook_deliveries(
    endpoint_id: UUID,
    limit: int = Query(
        default=webhooks.DELIVERY_LIST_LIMIT, ge=1, le=webhooks.DELIVERY_LIST_LIMIT
    ),
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookDeliveryResponse]:
    rows = await webhooks.list_deliveries(session, user.tenant_id, endpoint_id, limit=limit)
    return [WebhookDeliveryResponse.model_validate(row) for row in rows]
