from __future__ import annotations

from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.generation.deps import get_llm_client
from app.modules.generation.llm_client import LLMClient
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.pricing.quote import (
    export_quote_docx,
    generate_quote,
    latest_quote,
    serialize_quote,
)
from app.modules.pricing.schemas import PricingStrategyRequest, QuoteGenerateRequest
from app.modules.pricing.service import (
    generate_strategy,
    latest_strategy,
    price_band_for_project,
    sensitivity_for_project,
    serialize_strategy,
)

router = APIRouter(prefix="/api/v1/projects", tags=["pricing"])


@router.post("/{project_id}/pricing/strategy")
async def post_pricing_strategy(
    project_id: UUID,
    body: PricingStrategyRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    llm_client: LLMClient = Depends(get_llm_client),
) -> dict:
    """生成/重算报价策略。所有历史样本不足时显式返回 insufficient_data。"""
    row = await generate_strategy(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        body=body or PricingStrategyRequest(),
        llm_client=llm_client,
    )
    payload = serialize_strategy(row)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="project.pricing.strategy.generate",
        resource_type="bid_project",
        resource_id=str(project_id),
        data={
            "status": row.result_json.get("status"),
            "engine_version": row.engine_version,
            "narrative_source": (row.result_json.get("narrative") or {}).get("source"),
        },
    )
    await session.commit()
    return payload


@router.get("/{project_id}/pricing/strategy/latest")
async def get_latest_pricing_strategy(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await latest_strategy(session, user.tenant_id, project_id)
    return serialize_strategy(row)


@router.get("/{project_id}/pricing/price-band")
async def get_pricing_price_band(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await price_band_for_project(session, user.tenant_id, project_id)


@router.get("/{project_id}/pricing/sensitivity")
async def get_pricing_sensitivity(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await sensitivity_for_project(session, user.tenant_id, project_id)


@router.post("/{project_id}/pricing/quote")
async def post_pricing_quote(
    project_id: UUID,
    body: QuoteGenerateRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """从最新报价策略生成报价单快照；明细合计与总额不一致时 422。"""
    row = await generate_quote(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        body=body or QuoteGenerateRequest(),
    )
    payload = serialize_quote(row)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="project.pricing.quote.generate",
        resource_type="bid_project",
        resource_id=str(project_id),
        data={
            "quote_id": str(row.id),
            "quote_number": row.quote_number,
            "total_amount": payload["total_amount"],
            "price_source": row.price_source,
        },
    )
    await session.commit()
    return payload


@router.get("/{project_id}/pricing/quote/latest")
async def get_latest_pricing_quote(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await latest_quote(session, user.tenant_id, project_id)
    return serialize_quote(row)


@router.post("/{project_id}/pricing/quote/export/docx")
async def post_pricing_quote_export(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    data, filename = await export_quote_docx(
        session, tenant_id=user.tenant_id, project_id=project_id
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="project.pricing.quote.export",
        resource_type="bid_project",
        resource_id=str(project_id),
        data={"filename": filename},
    )
    await session.commit()
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
