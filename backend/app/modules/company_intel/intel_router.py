from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.company_intel import watchlist_service
from app.modules.company_intel.watchlist_models import IntelWatchlist
from app.modules.costs.quota import QuotaReservation, release_paid_call, reserve_paid_call
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.contract import DataError, DataResult
from app.modules.gateway.deps import get_enterprise_intel_adapter
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1/company-intel", tags=["company-intel"])


class DueDiligenceRequest(BaseModel):
    ent_name: str = Field(min_length=1, max_length=200)
    sections: str | None = None
    include_judicial: bool = True
    digest_only: bool = True


class BatchVerifyRequest(BaseModel):
    ent_names: list[str] = Field(min_length=1, max_length=100)


class CompareRequest(BaseModel):
    ent_names: list[str] = Field(min_length=2, max_length=5)


class WatchlistCreate(BaseModel):
    ent_name: str = Field(min_length=1, max_length=300)
    label: str | None = Field(default=None, max_length=300)
    sections: str | None = None


class WatchlistUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=300)
    sections: str | None = None
    active: bool | None = None


def _watchlist_payload(item: IntelWatchlist) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "ent_name": item.ent_name,
        "label": item.label,
        "sections": item.sections,
        "active": item.active,
        "last_checked_at": item.last_checked_at,
        "last_status": item.last_status,
        "last_digest": item.last_digest,
        "last_error": item.last_error,
        "created_at": item.created_at,
    }


def _error_payload(error: DataError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "field": error.field,
        "raw_payload_ref": error.raw_payload_ref,
    }


def _result_payload(result: DataResult[Any]) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "data": result.data,
        "source": result.source,
        "source_url": result.source_url,
        "fetched_at": result.fetched_at,
        "order_no": result.order_no,
        "charged": result.charged,
        "api_calls": result.api_calls,
        "billing_units": result.billing_units,
        "warnings": [_error_payload(item) for item in result.warnings],
        "error": _error_payload(result.error) if result.error else None,
    }


async def _execute(
    *,
    user: CurrentUser,
    session: AsyncSession,
    tool: str,
    units: int,
    operation: str,
    resource_id: str,
    arguments: dict[str, Any],
    call: Any,
) -> dict[str, Any]:
    reservation = await reserve_paid_call(user.tenant_id, amount=units)
    try:
        result = await call()
    except Exception:
        await release_paid_call(reservation)
        raise
    if not result.is_success or not result.charged:
        await release_paid_call(reservation)
    else:
        actual = min(result.billing_units or units, units)
        if actual < reservation.amount:
            await release_paid_call(
                QuotaReservation(
                    tenant_id=reservation.tenant_id,
                    periods=reservation.periods,
                    amount=reservation.amount - actual,
                )
            )
    await record_gateway_call(
        session,
        tenant_id=user.tenant_id,
        pcode=f"mcp:{tool}",
        operation=f"company_intel.{operation}",
        status=result.status.value,
        order_no=result.order_no,
        charged=result.charged,
        rows=result.api_calls or result.billing_units or units,
        query_hash_value=query_hash(arguments),
        resource_type="company_intel",
        resource_id=resource_id,
        created_by=user.id,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=f"company_intel.{operation}",
        resource_type="company_intel",
        resource_id=resource_id,
        data={
            "tool": tool,
            "status": result.status.value,
            "api_calls": result.api_calls,
            "billing_units": result.billing_units,
            "arguments": arguments,
        },
    )
    await session.commit()
    return _result_payload(result)


@router.get("/resolve")
async def resolve_company(
    keyword: str = Query(min_length=1, max_length=200),
    region: str | None = Query(default=None, max_length=100),
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    arguments = {"keyword": keyword}
    if region:
        arguments["region"] = region
    return await _execute(
        user=user,
        session=session,
        tool="resolve_entity",
        units=adapter.estimate_units("resolve_entity"),
        operation="resolve",
        resource_id=keyword,
        arguments=arguments,
        call=lambda: adapter.resolve(keyword, region=region),
    )


@router.get("/profile")
async def company_profile(
    ent_name: str = Query(min_length=1, max_length=200),
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    return await _execute(
        user=user,
        session=session,
        tool="enterprise_basic_profile",
        units=adapter.estimate_units("enterprise_basic_profile"),
        operation="profile",
        resource_id=ent_name,
        arguments={"ent_name": ent_name},
        call=lambda: adapter.basic_profile(ent_name),
    )


@router.get("/credit")
async def company_credit(
    ent_name: str = Query(min_length=1, max_length=200),
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    return await _execute(
        user=user,
        session=session,
        tool="enterprise_credit_score",
        units=adapter.estimate_units("enterprise_credit_score"),
        operation="credit",
        resource_id=ent_name,
        arguments={"ent_name": ent_name},
        call=lambda: adapter.credit_score(ent_name),
    )


@router.get("/relations")
async def company_relations(
    ent_name: str = Query(min_length=1, max_length=200),
    digest_only: bool = True,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    return await _execute(
        user=user,
        session=session,
        tool="enterprise_graph_relations",
        units=adapter.estimate_units("enterprise_graph_relations"),
        operation="relations",
        resource_id=ent_name,
        arguments={"ent_name": ent_name, "digest_only": digest_only},
        call=lambda: adapter.graph_relations(ent_name, digest_only=digest_only),
    )


@router.post("/batch")
async def company_batch_verify(
    body: BatchVerifyRequest,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    names = [name.strip() for name in body.ent_names if name.strip()]
    if not names:
        raise ValidationAppError("ent_names must not be empty")
    tool = "batch_query_companies"
    return await _execute(
        user=user,
        session=session,
        tool=tool,
        units=adapter.estimate_units(tool),
        operation="batch",
        resource_id=names[0],
        arguments={"ent_names": names},
        call=lambda: adapter.batch_verify(names),
    )


@router.post("/compare")
async def company_compare(
    body: CompareRequest,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    names = [name.strip() for name in body.ent_names if name.strip()]
    if len(names) < 2:
        raise ValidationAppError("ent_names requires 2-5 companies")
    tool = "compare_enterprises"
    return await _execute(
        user=user,
        session=session,
        tool=tool,
        units=10 * len(names),
        operation="compare",
        resource_id=names[0],
        arguments={"ent_names": names},
        call=lambda: adapter.compare(names),
    )


@router.post("/watchlist", status_code=201)
async def create_watchlist_item(
    body: WatchlistCreate,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    item = await watchlist_service.create_item(
        session,
        user,
        ent_name=body.ent_name,
        label=body.label,
        sections=body.sections,
    )
    return _watchlist_payload(item)


@router.get("/watchlist")
async def list_watchlist_items(
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    items = await watchlist_service.list_items(session, user)
    return [_watchlist_payload(item) for item in items]


@router.patch("/watchlist/{item_id}")
async def update_watchlist_item(
    item_id: UUID,
    body: WatchlistUpdate,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    item = await watchlist_service.update_item(
        session,
        user,
        item_id,
        label=body.label,
        sections=body.sections,
        active=body.active,
    )
    return _watchlist_payload(item)


@router.delete("/watchlist/{item_id}", status_code=204)
async def delete_watchlist_item(
    item_id: UUID,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await watchlist_service.delete_item(session, user, item_id)


@router.post("/watchlist/{item_id}/run")
async def run_watchlist_item(
    item_id: UUID,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    item = await watchlist_service.get_item(session, user, item_id)
    item, result = await watchlist_service.run_item(session, item, adapter)
    return {
        "item": _watchlist_payload(item),
        "status": result.status.value,
        "api_calls": result.api_calls,
        "billing_units": result.billing_units,
        "charged": result.charged,
    }


@router.post("/due-diligence")
async def company_due_diligence(
    body: DueDiligenceRequest,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
    adapter: EnterpriseIntelAdapter = Depends(get_enterprise_intel_adapter),
) -> dict[str, Any]:
    tool = "enterprise_due_diligence_snapshot"
    arguments = {
        "ent_name": body.ent_name,
        "include_judicial": body.include_judicial,
        "digest_only": body.digest_only,
    }
    if body.sections:
        arguments["sections"] = body.sections
    return await _execute(
        user=user,
        session=session,
        tool=tool,
        units=adapter.estimate_units(tool, sections=body.sections),
        operation="due_diligence",
        resource_id=body.ent_name,
        arguments=arguments,
        call=lambda: adapter.due_diligence(
            body.ent_name,
            sections=body.sections,
            include_judicial=body.include_judicial,
            digest_only=body.digest_only,
        ),
    )
