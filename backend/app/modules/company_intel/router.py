from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.assets.schemas import BidSyncRequest, CompanyBidResponse
from app.modules.assets.service import get_company
from app.modules.audit.service import AuditService
from app.modules.company_intel.service import (
    list_company_bids,
    profile_payload,
    sync_company_bids,
)
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.company_adapter import KeepRiskCompanyIntelAdapter
from app.modules.gateway.deps import get_company_intel_adapter
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["company-intel"])


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    action: str,
    resource_id: str,
    data: dict,
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type="company",
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.get("/companies/{company_id}/profile")
async def get_company_profile(
    company_id: UUID,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskCompanyIntelAdapter = Depends(get_company_intel_adapter),
) -> dict[str, object]:
    company = await get_company(session, user.tenant_id, company_id)
    reservation = await reserve_paid_call(user.tenant_id)
    try:
        result = await adapter.fetch_profile(company.name)
    except Exception:
        await release_paid_call(reservation)
        raise
    if not result.charged:
        await release_paid_call(reservation)
    payload = profile_payload(company, result)
    await record_gateway_call(
        session,
        tenant_id=user.tenant_id,
        pcode="E_007_1341",
        operation="company.profile",
        status=result.status.value,
        order_no=result.order_no,
        charged=result.charged,
        rows=len(result.data.entries) if result.data else 0,
        query_hash_value=query_hash({"entName": company.name}),
        resource_type="company",
        resource_id=str(company.id),
        created_by=user.id,
    )
    await _audit(
        session,
        user,
        "asset.company.profile",
        str(company.id),
        {
            "status": result.status.value,
            "order_no": result.order_no,
            "charged": result.charged,
        },
    )
    return payload


@router.post("/companies/{company_id}/bids/sync")
async def post_company_bid_sync(
    company_id: UUID,
    body: BidSyncRequest,
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskCompanyIntelAdapter = Depends(get_company_intel_adapter),
) -> dict[str, object]:
    data = await sync_company_bids(
        session,
        tenant_id=user.tenant_id,
        company_id=company_id,
        roles=list(body.roles),
        adapter=adapter,
    )
    for result in data["results"]:
        await record_gateway_call(
            session,
            tenant_id=user.tenant_id,
            pcode="E_007_1271",
            operation=f"company.bids.{result['role']}",
            status=result["status"],
            order_no=result["order_no"],
            charged=result["charged"],
            rows=result["fetched"],
            query_hash_value=query_hash(
                {"company": data["company_name"], "role": result["role"]}
            ),
            resource_type="company",
            resource_id=str(company_id),
            created_by=user.id,
        )
    await _audit(
        session,
        user,
        "asset.company.bids.sync",
        str(company_id),
        {
            "roles": list(body.roles),
            "upserted": data["upserted"],
            "results": [
                {
                    "role": result["role"],
                    "status": result["status"],
                    "order_no": result["order_no"],
                    "charged": result["charged"],
                }
                for result in data["results"]
            ],
        },
    )
    return data


@router.get("/companies/{company_id}/bids")
async def get_company_bid_list(
    company_id: UUID,
    role: Literal["winner", "tenderer", "agency"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows, total = await list_company_bids(
        session,
        tenant_id=user.tenant_id,
        company_id=company_id,
        role=role,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [CompanyBidResponse.model_validate(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
