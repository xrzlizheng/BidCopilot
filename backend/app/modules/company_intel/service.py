import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import QuotaExceededError
from app.db.repository import TenantRepository
from app.modules.assets.models import Company, CompanyBid
from app.modules.assets.service import get_company
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.gateway.company_adapter import (
    CompanyProfileEntry,
    CompanyProfilePage,
    KeepRiskCompanyIntelAdapter,
)
from app.modules.gateway.contract import DataError, DataResult


class CompanyBidRepository(TenantRepository[CompanyBid]):
    model = CompanyBid


def error_payload(error: DataError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "field": error.field,
        "raw_payload_ref": error.raw_payload_ref,
    }


def profile_entry_payload(entry: CompanyProfileEntry) -> dict[str, Any]:
    return {
        "subject_name": entry.subject_name,
        "subject_status": entry.subject_status,
        "subject_type": entry.subject_type,
        "region": entry.region,
        "roles": entry.roles,
        "related_count": entry.related_count,
        "bidder_count": entry.bidder_count,
        "winner_count": entry.winner_count,
        "agent_count": entry.agent_count,
        "tender_count": entry.tender_count,
        "latest_publish_at": entry.latest_publish_at,
        "latest_title": entry.latest_title,
        "latest_role": entry.latest_role,
        "info_type": entry.info_type,
    }


def profile_payload(company: Company, result: DataResult[CompanyProfilePage]) -> dict[str, Any]:
    page = result.data or CompanyProfilePage(total=0)
    return {
        "status": result.status.value,
        "company_id": company.id,
        "company_name": company.name,
        "source": result.source,
        "fetched_at": result.fetched_at,
        "order_no": result.order_no,
        "charged": result.charged,
        "total": page.total,
        "entries": [profile_entry_payload(entry) for entry in page.entries],
        "warnings": [error_payload(warning) for warning in result.warnings],
        "error": error_payload(result.error) if result.error else None,
    }


def _bid_dedup_key(
    company_id: UUID,
    role: str,
    source_id: str | None,
    title: str,
    project_code: str | None,
    publish_at: datetime | None,
) -> str:
    identity = source_id or "|".join(
        [project_code or "", title, publish_at.isoformat() if publish_at else ""]
    )
    return hashlib.sha256(f"{company_id}|{role}|{identity}".encode()).hexdigest()


async def sync_company_bids(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    company_id: UUID,
    roles: list[str],
    adapter: KeepRiskCompanyIntelAdapter,
) -> dict[str, Any]:
    company = await get_company(session, tenant_id, company_id)
    results: list[dict[str, Any]] = []
    total_upserted = 0
    for role in dict.fromkeys(roles):
        try:
            reservation = await reserve_paid_call(tenant_id)
        except QuotaExceededError as exc:
            results.append(
                {
                    "role": role,
                    "status": exc.code,
                    "fetched": 0,
                    "upserted": 0,
                    "order_no": None,
                    "charged": False,
                    "warnings": [],
                    "error": {"code": exc.code, "message": exc.message, "field": None},
                }
            )
            break
        try:
            result = await adapter.fetch_bids(company.name, role=role)
        except Exception:
            await release_paid_call(reservation)
            raise
        if not result.charged:
            await release_paid_call(reservation)
        role_result: dict[str, Any] = {
            "role": role,
            "status": result.status.value,
            "fetched": len(result.data or []),
            "upserted": 0,
            "order_no": result.order_no,
            "charged": result.charged,
            "warnings": [error_payload(warning) for warning in result.warnings],
            "error": error_payload(result.error) if result.error else None,
        }
        if result.is_success:
            for item in result.data or []:
                values = {
                    "company_id": company.id,
                    "role": role,
                    "source": item.source,
                    "source_id": item.source_id,
                    "title": item.title,
                    "project_name": item.project_name,
                    "project_code": item.project_code,
                    "buyer": item.buyer,
                    "agency": item.agency,
                    "winner": item.winner,
                    "budget_amount": item.budget_amount,
                    "win_bid_amount": item.win_bid_amount,
                    "publish_at": item.publish_at,
                    "info_type": item.info_type,
                    "source_url": item.source_url,
                    "fetched_at": item.fetched_at,
                    "raw_snapshot_ref": result.raw_ref,
                }
                statement = (
                    insert(CompanyBid)
                    .values(
                        tenant_id=tenant_id,
                        dedup_key=_bid_dedup_key(
                            company.id,
                            role,
                            item.source_id,
                            item.title,
                            item.project_code,
                            item.publish_at,
                        ),
                        **values,
                    )
                    .on_conflict_do_update(
                        index_elements=["tenant_id", "dedup_key"],
                        set_=values,
                    )
                )
                await session.execute(statement)
                role_result["upserted"] += 1
                total_upserted += 1
        results.append(role_result)
    await session.commit()
    return {
        "company_id": company.id,
        "company_name": company.name,
        "results": results,
        "upserted": total_upserted,
    }


async def list_company_bids(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    company_id: UUID,
    role: str | None,
    page: int,
    page_size: int,
) -> tuple[list[CompanyBid], int]:
    await get_company(session, tenant_id, company_id)
    repo = CompanyBidRepository(session, tenant_id)
    statement = repo.scoped(select(CompanyBid).where(CompanyBid.company_id == company_id))
    if role:
        statement = statement.where(CompanyBid.role == role)
    total = (
        await session.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                statement.order_by(CompanyBid.publish_at.desc().nullslast(), CompanyBid.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total
