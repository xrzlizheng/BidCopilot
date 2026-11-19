from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.db.session import get_session
from app.modules.gateway.deps import get_keeprisk_adapter
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.opportunities.schemas import (
    OpportunitySubscriptionCreateRequest,
    OpportunitySubscriptionResponse,
    OpportunitySubscriptionUpdateRequest,
    SyncRunResponse,
)
from app.modules.opportunities.subscriptions import (
    create_subscription,
    delete_subscription,
    list_subscriptions,
    owned_subscription,
    run_subscription,
    subscription_lock,
    update_subscription,
)

router = APIRouter(prefix="/api/v1/opportunity-subscriptions", tags=["opportunities"])


@router.get("")
async def list_opportunity_subscriptions(
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await list_subscriptions(session, tenant_id=user.tenant_id, user_id=user.id)
    return {
        "items": [OpportunitySubscriptionResponse.model_validate(row) for row in rows],
        "total": len(rows),
    }


@router.post("", response_model=OpportunitySubscriptionResponse)
async def create_opportunity_subscription(
    body: OpportunitySubscriptionCreateRequest,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
) -> OpportunitySubscriptionResponse:
    subscription = await create_subscription(
        session, tenant_id=user.tenant_id, user_id=user.id, body=body
    )
    return OpportunitySubscriptionResponse.model_validate(subscription)


@router.patch("/{subscription_id}", response_model=OpportunitySubscriptionResponse)
async def update_opportunity_subscription(
    subscription_id: UUID,
    body: OpportunitySubscriptionUpdateRequest,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
) -> OpportunitySubscriptionResponse:
    subscription = await update_subscription(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        subscription_id=subscription_id,
        body=body,
    )
    return OpportunitySubscriptionResponse.model_validate(subscription)


@router.delete("/{subscription_id}")
async def delete_opportunity_subscription(
    subscription_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await delete_subscription(
        session, tenant_id=user.tenant_id, user_id=user.id, subscription_id=subscription_id
    )
    return {"deleted": True}


@router.post("/{subscription_id}/run", response_model=SyncRunResponse)
async def run_opportunity_subscription(
    subscription_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskTenderAdapter = Depends(get_keeprisk_adapter),
) -> SyncRunResponse:
    subscription = await owned_subscription(
        session, tenant_id=user.tenant_id, user_id=user.id, subscription_id=subscription_id
    )
    async with subscription_lock(subscription_id) as acquired:
        if not acquired:
            raise ConflictError("该订阅正在同步中，请稍后重试")
        run = await run_subscription(session, adapter, subscription, actor_id=user.id)
        payload = SyncRunResponse.model_validate(run)
    return payload

