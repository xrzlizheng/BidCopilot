from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import QuotaExceededError
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter, OpportunityQuery
from app.modules.opportunities.models import (
    AwardRecord,
    Opportunity,
    OpportunitySnapshot,
    SourceSyncRun,
)


class OpportunitySyncService:
    def __init__(self, session: AsyncSession, adapter: KeepRiskTenderAdapter) -> None:
        self.session = session
        self.adapter = adapter

    async def run(self, run_id: UUID) -> SourceSyncRun:
        run = await self.session.get(SourceSyncRun, run_id)
        if run is None:
            raise ValueError("sync run not found")
        if run.status == "succeeded":
            return run

        run.status = "running"
        run.started_at = datetime.now(UTC)
        await self.session.commit()

        query = OpportunityQuery.from_json(run.query_json)
        try:
            reservation = await reserve_paid_call(run.tenant_id)
        except QuotaExceededError as exc:
            run.status = "failed"
            run.error_code = exc.code
            run.error_message = exc.message
            run.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise
        try:
            result = await self.adapter.fetch_page(query)
        except Exception:
            await release_paid_call(reservation)
            raise
        if not result.charged:
            await release_paid_call(reservation)
        run.rows_fetched = len(result.data or [])
        run.warnings_count = len(result.warnings)
        run.warnings = [
            {"code": warning.code, "message": warning.message, "field": warning.field}
            for warning in result.warnings
        ]
        run.snapshot_ref = result.raw_ref
        run.gateway_order_no = result.order_no
        run.charged = result.charged
        # DataResult.status 原样落库：ok/empty/rate_limited/unauthorized/... 不再二值化。
        run.source_status = result.status.value

        if not result.is_success:
            run.status = "failed"
            run.error_code = result.error.code if result.error else result.status.value
            run.error_message = result.error.message if result.error else result.status.value
            run.finished_at = datetime.now(UTC)
            await self.session.commit()
            return run

        upserted = 0
        # on_conflict_do_update 让 rows_upserted 无法区分新增与刷新，新商机水位单独 diff。
        dedup_keys = [item.dedup_key for item in result.data or []]
        existing_keys: set[str] = set()
        if dedup_keys:
            existing_keys = set(
                (
                    await self.session.execute(
                        select(Opportunity.dedup_key).where(
                            Opportunity.tenant_id == run.tenant_id,
                            Opportunity.dedup_key.in_(dedup_keys),
                        )
                    )
                )
                .scalars()
                .all()
            )
        new_keys: set[str] = set()
        for item in result.data or []:
            if item.dedup_key not in existing_keys:
                new_keys.add(item.dedup_key)
            update_values = {
                "source_id": item.source_id,
                "title": item.title,
                "project_code": item.project_code,
                "project_name": item.project_name,
                "buyer": item.buyer,
                "agency": item.agency,
                "winner": item.winner,
                "region": item.region,
                "industry": item.industry,
                "budget_amount": item.budget_amount,
                "win_bid_amount": item.win_bid_amount,
                "publish_at": item.publish_at,
                "info_type": item.info_type,
                "source_url": item.source_url,
                "confidence": item.confidence,
                "fetched_at": item.fetched_at,
                "raw_snapshot_ref": result.raw_ref,
            }
            if item.detail_status in {"full", "attachment"}:
                update_values["detail_status"] = item.detail_status
                if item.detail_status == "full":
                    update_values.update(
                        {
                            "content_html": item.content_html,
                            "content_text": item.content_text,
                            "content_fetched_at": item.content_fetched_at,
                        }
                    )
                else:
                    update_values.update(
                        {
                            "content_html": None,
                            "content_text": None,
                            "content_fetched_at": None,
                        }
                    )
            statement = (
                insert(Opportunity)
                .values(
                    tenant_id=run.tenant_id,
                    source=item.source,
                    dedup_key=item.dedup_key,
                    detail_status=item.detail_status,
                    content_html=item.content_html,
                    content_text=item.content_text,
                    content_fetched_at=item.content_fetched_at,
                    **update_values,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "source", "dedup_key"],
                    set_=update_values,
                )
                .returning(Opportunity.id)
            )
            opportunity_id = (await self.session.execute(statement)).scalar_one()
            self.session.add(
                OpportunitySnapshot(
                    tenant_id=run.tenant_id,
                    opportunity_id=opportunity_id,
                    raw_snapshot_ref=result.raw_ref or "",
                    fetched_at=item.fetched_at,
                )
            )
            for award in item.awards:
                await self.session.execute(
                    insert(AwardRecord)
                    .values(
                        tenant_id=run.tenant_id,
                        opportunity_id=opportunity_id,
                        dedup_key=award.dedup_key,
                        project_code=award.project_code,
                        project_name=award.project_name,
                        winner=award.winner,
                        win_amount=award.win_amount,
                        award_at=award.award_at,
                        source=award.source,
                        source_url=award.source_url,
                        fetched_at=award.fetched_at,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "dedup_key"])
                )
            upserted += 1

        run.rows_upserted = upserted
        run.rows_new = len(new_keys)
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        await self.session.commit()
        return run


async def get_sync_run(session: AsyncSession, tenant_id: UUID, run_id: UUID) -> SourceSyncRun | None:
    return (
        await session.execute(
            select(SourceSyncRun).where(
                SourceSyncRun.tenant_id == tenant_id,
                SourceSyncRun.id == run_id,
            )
        )
    ).scalar_one_or_none()
