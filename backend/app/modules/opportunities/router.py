import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.region import filter_terms
from app.db.locks import advisory_lock
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.company_adapter import KeepRiskCompanyIntelAdapter
from app.modules.gateway.deps import get_company_intel_adapter, get_keeprisk_adapter
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.opportunities.auto_match import (
    get_auto_match_settings,
    match_response_payload,
    recompute_opportunity_match,
    run_auto_match_for_tenant,
    update_auto_match_settings,
)
from app.modules.opportunities.buyer_intel import get_buyer_intel, sync_buyer_intel
from app.modules.opportunities.matching import (
    MatchPreference,
    count_match_hits,
    explain_opportunity,
    match_score_expression,
    preference_from_query,
    preferences_from_subscriptions,
)
from app.modules.opportunities.models import (
    AwardRecord,
    BuyerWatchlist,
    Opportunity,
    OpportunityBuyerIntel,
    OpportunityFavorite,
    OpportunityMatch,
    OpportunitySubscription,
    SourceSyncRun,
)
from app.modules.opportunities.repository import (
    AwardRepository,
    BuyerWatchlistRepository,
    OpportunityMatchRepository,
    OpportunityRepository,
    SubscriptionRepository,
)
from app.modules.opportunities.schemas import (
    AutoMatchRunRequest,
    AutoMatchRunResponse,
    AutoMatchSettingsResponse,
    AutoMatchSettingsUpdate,
    AwardResponse,
    BuyerWatchlistCreate,
    BuyerWatchlistResponse,
    CoverageItem,
    CoverageResponse,
    FavoriteResponse,
    OpportunityBuyerIntelResponse,
    OpportunityDetail,
    OpportunityExplanation,
    OpportunityListItem,
    OpportunityMatchResponse,
    OpportunityMatchSummary,
    OpportunitySyncRequest,
    SyncRunResponse,
)
from app.modules.opportunities.sync import OpportunitySyncService, get_sync_run

logger = logging.getLogger("bidcopilot.opportunities")

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


@router.get("")
async def list_opportunities(
    q: str | None = None,
    region: str | None = None,
    industry: str | None = None,
    budget_min: Decimal | None = None,
    budget_max: Decimal | None = None,
    publish_from: date | None = None,
    publish_to: date | None = None,
    info_type: str | None = None,
    favorite: bool | None = None,
    buyer_watch: bool | None = None,
    explain: bool = True,
    created_after: datetime | None = None,
    order_by: str = Query(default="publish_at", pattern="^(publish_at|budget|match)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    repo = OpportunityRepository(session, user.tenant_id)
    statement = repo.scoped(select(Opportunity))
    if q:
        statement = statement.where(Opportunity.title.ilike(f"%{q}%"))
    if region:
        # 能唯一确定的行政区（省名、带「市」的地级市、全国唯一的区县）用前缀匹配，
        # 避免「南」命中南京/济南/南宁；其余（如「朝阳」可能指北京朝阳区或辽宁
        # 朝阳市）退回模糊匹配——宁可多返回几条，也不能漏掉用户真正要找的。
        exact, term = filter_terms(region)
        if exact:
            statement = statement.where(Opportunity.region.ilike(f"{term}%"))
        else:
            statement = statement.where(Opportunity.region.ilike(f"%{term}%"))
    if industry:
        statement = statement.where(Opportunity.industry.contains([industry]))
    if budget_min is not None:
        statement = statement.where(Opportunity.budget_amount >= budget_min)
    if budget_max is not None:
        statement = statement.where(Opportunity.budget_amount <= budget_max)
    if publish_from is not None:
        statement = statement.where(Opportunity.publish_at >= _day_start(publish_from))
    if publish_to is not None:
        statement = statement.where(Opportunity.publish_at < _day_start(publish_to + timedelta(days=1)))
    if info_type:
        statement = statement.where(Opportunity.info_type == info_type)
    if created_after is not None:
        statement = statement.where(Opportunity.created_at >= created_after)
    favorite_ids = select(OpportunityFavorite.opportunity_id).where(
        OpportunityFavorite.tenant_id == user.tenant_id,
        OpportunityFavorite.user_id == user.id,
    )
    if favorite is True:
        statement = statement.where(Opportunity.id.in_(favorite_ids))
    elif favorite is False:
        statement = statement.where(Opportunity.id.not_in(favorite_ids))

    watch_rows = list(
        (
            await session.execute(
                BuyerWatchlistRepository(session, user.tenant_id)
                .scoped(select(BuyerWatchlist))
                .order_by(BuyerWatchlist.created_at.desc())
            )
        ).scalars()
    )
    watched_names = {row.buyer_name for row in watch_rows}
    if buyer_watch is True:
        statement = statement.where(Opportunity.buyer.in_(watched_names))
    elif buyer_watch is False:
        statement = statement.where(Opportunity.buyer.not_in(watched_names))

    total = (await session.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    preferences: list[MatchPreference] | None = None
    if order_by == "match" or explain:
        subscriptions = (
            await session.execute(
                SubscriptionRepository(session, user.tenant_id)
                .scoped(select(OpportunitySubscription))
                .where(
                    OpportunitySubscription.user_id == user.id,
                    OpportunitySubscription.enabled.is_(True),
                )
            )
        ).scalars().all()
        preferences = preferences_from_subscriptions(subscriptions)
        if order_by == "match" and not preferences:
            raise ValidationAppError(
                "匹配度排序需要至少一个启用中且带条件（关键词/地区/行业/预算）的订阅"
            )
    if preferences is not None:
        statement = statement.order_by(
            match_score_expression(preferences).desc(),
            Opportunity.publish_at.desc().nullslast(),
            Opportunity.id.desc(),
        )
    elif order_by == "budget":
        statement = statement.order_by(
            Opportunity.budget_amount.desc().nullslast(), Opportunity.id.desc()
        )
    else:
        statement = statement.order_by(
            Opportunity.publish_at.desc().nullslast(), Opportunity.id.desc()
        )
    rows = (
        await session.execute(
            statement.offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    match_map: dict[UUID, OpportunityMatch] = {}
    if rows:
        persisted_matches = list(
            (
                await session.execute(
                    OpportunityMatchRepository(session, user.tenant_id)
                    .scoped(select(OpportunityMatch))
                    .where(OpportunityMatch.opportunity_id.in_([row.id for row in rows]))
                )
            ).scalars()
        )
        match_map = {row.opportunity_id: row for row in persisted_matches}
    intel_map: dict[UUID, OpportunityBuyerIntel] = {}
    if explain and rows:
        intel_rows = list(
            (
                await session.execute(
                    select(OpportunityBuyerIntel).where(
                        OpportunityBuyerIntel.tenant_id == user.tenant_id,
                        OpportunityBuyerIntel.opportunity_id.in_([row.id for row in rows]),
                    )
                )
            ).scalars()
        )
        intel_map = {row.opportunity_id: row for row in intel_rows}
    watched_casefold = {name.casefold() for name in watched_names}
    items: list[OpportunityListItem] = []
    for row in rows:
        item = OpportunityListItem.model_validate(row)
        persisted = match_map.get(row.id)
        if persisted is not None:
            item.match = OpportunityMatchSummary(
                match_score=persisted.match_score,
                hit_groups=list(persisted.hit_groups_json or []),
                qualified=persisted.qualified,
                decision=persisted.decision,
                computed_at=persisted.computed_at,
            )
        if preferences is not None:
            item.match_hits = count_match_hits(preferences, row)
        if explain:
            intel = intel_map.get(row.id)
            intel_summary = (
                {
                    "recent_wins_12m": intel.recent_wins_12m,
                    "existing_suppliers": [
                        str(entry.get("name") or entry.get("supplier") or "")
                        for entry in (intel.existing_suppliers or [])
                        if isinstance(entry, dict)
                    ],
                }
                if intel is not None
                else None
            )
            item.explanation = OpportunityExplanation.model_validate(
                explain_opportunity(
                    preferences or [],
                    row,
                    buyer_watched=bool(row.buyer and row.buyer.casefold() in watched_casefold),
                    buyer_intel=intel_summary,
                )
            )
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "order_by": order_by,
    }


@router.get("/buyer-watchlist", response_model=list[BuyerWatchlistResponse])
async def get_buyer_watchlist(
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> list[BuyerWatchlistResponse]:
    rows = list(
        (
            await session.execute(
                BuyerWatchlistRepository(session, user.tenant_id)
                .scoped(select(BuyerWatchlist))
                .order_by(BuyerWatchlist.created_at.desc())
            )
        ).scalars()
    )
    return [BuyerWatchlistResponse.model_validate(row) for row in rows]


@router.post("/buyer-watchlist", response_model=BuyerWatchlistResponse, status_code=201)
async def post_buyer_watchlist(
    body: BuyerWatchlistCreate,
    user: CurrentUser = Depends(require_permission("opportunity:write")),
    session: AsyncSession = Depends(get_session),
) -> BuyerWatchlistResponse:
    buyer_name = body.buyer_name.strip()
    if not buyer_name:
        raise ValidationAppError("采购人名称不能为空")
    existing = (
        await session.execute(
            BuyerWatchlistRepository(session, user.tenant_id)
            .scoped(select(BuyerWatchlist))
            .where(BuyerWatchlist.buyer_name == buyer_name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("该采购人已在关注清单")
    row = BuyerWatchlist(
        tenant_id=user.tenant_id,
        buyer_name=buyer_name,
        note=body.note,
        created_by=user.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="opportunity.buyer_watchlist.create",
        resource_type="buyer_watchlist",
        resource_id=str(row.id),
        data={"buyer_name": buyer_name},
    )
    await session.commit()
    return BuyerWatchlistResponse.model_validate(row)


@router.delete("/buyer-watchlist/{watch_id}", status_code=204)
async def delete_buyer_watchlist(
    watch_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = (
        await session.execute(
            BuyerWatchlistRepository(session, user.tenant_id)
            .scoped(select(BuyerWatchlist))
            .where(BuyerWatchlist.id == watch_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("关注采购人不存在")
    await session.delete(row)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="opportunity.buyer_watchlist.delete",
        resource_type="buyer_watchlist",
        resource_id=str(watch_id),
        data={"buyer_name": row.buyer_name},
    )
    await session.commit()


@router.get("/coverage", response_model=CoverageResponse)
async def get_coverage(
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> CoverageResponse:
    repo = OpportunityRepository(session, user.tenant_id)
    total = int(
        (
            await session.execute(
                repo.scoped(select(func.count()).select_from(Opportunity))
            )
        ).scalar_one()
    )
    subscriptions = list(
        (
            await session.execute(
                SubscriptionRepository(session, user.tenant_id)
                .scoped(select(OpportunitySubscription))
                .where(
                    OpportunitySubscription.user_id == user.id,
                    OpportunitySubscription.enabled.is_(True),
                )
            )
        ).scalars()
    )
    items: list[CoverageItem] = []
    for subscription in subscriptions:
        preference = preference_from_query(subscription.query_json or {})
        if not preference.has_conditions:
            items.append(
                CoverageItem(
                    subscription_id=subscription.id,
                    name=subscription.name,
                    keywords=[],
                    matched=0,
                    zero_hit=True,
                )
            )
            continue
        matched = int(
            (
                await session.execute(
                    repo.scoped(select(func.count()).select_from(Opportunity)).where(
                        match_score_expression([preference]) > 0
                    )
                )
            ).scalar_one()
        )
        items.append(
            CoverageItem(
                subscription_id=subscription.id,
                name=subscription.name,
                keywords=list(preference.keywords),
                matched=matched,
                zero_hit=matched == 0,
            )
        )
    return CoverageResponse(
        total_opportunities=total,
        active_subscriptions=len(subscriptions),
        items=items,
    )


@router.get("/auto-match/settings", response_model=AutoMatchSettingsResponse)
async def read_auto_match_settings(
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> AutoMatchSettingsResponse:
    settings = await get_auto_match_settings(session, user.tenant_id)
    return AutoMatchSettingsResponse(**settings)


@router.patch("/auto-match/settings", response_model=AutoMatchSettingsResponse)
async def patch_auto_match_settings(
    body: AutoMatchSettingsUpdate,
    user: CurrentUser = Depends(require_permission("opportunity:write")),
    session: AsyncSession = Depends(get_session),
) -> AutoMatchSettingsResponse:
    settings = await update_auto_match_settings(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        updates=body.model_dump(exclude_unset=True),
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="opportunity.auto_match_settings_updated",
        resource_type="tenant_settings",
        resource_id="opportunity_auto_match",
        data=settings,
    )
    await session.commit()
    return AutoMatchSettingsResponse(**settings)


@router.post("/auto-match/run", response_model=AutoMatchRunResponse)
async def run_auto_match(
    body: AutoMatchRunRequest | None = None,
    user: CurrentUser = Depends(require_permission("opportunity:write")),
    session: AsyncSession = Depends(get_session),
) -> AutoMatchRunResponse:
    payload = body or AutoMatchRunRequest()
    result = await run_auto_match_for_tenant(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        actor_id=user.id,
        limit=payload.limit,
        force=payload.force,
    )
    return AutoMatchRunResponse(**result)


@router.post("/{opportunity_id}/match", response_model=OpportunityMatchResponse)
async def recompute_match(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:write")),
    session: AsyncSession = Depends(get_session),
) -> OpportunityMatchResponse:
    row, _, _ = await recompute_opportunity_match(
        session,
        tenant_id=user.tenant_id,
        opportunity_id=opportunity_id,
        user_id=user.id,
        actor_id=user.id,
    )
    return OpportunityMatchResponse(**match_response_payload(row))


@router.get("/{opportunity_id}/match", response_model=OpportunityMatchResponse)
async def read_match(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> OpportunityMatchResponse:
    await OpportunityRepository(session, user.tenant_id).get_or_404(opportunity_id)
    row = (
        await session.execute(
            OpportunityMatchRepository(session, user.tenant_id)
            .scoped(select(OpportunityMatch))
            .where(OpportunityMatch.opportunity_id == opportunity_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    return OpportunityMatchResponse(**match_response_payload(row))


@router.get("/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity(
    opportunity_id: UUID,
    refresh: bool = Query(default=False, description="失败后显式重试详情拉取"),
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskTenderAdapter = Depends(get_keeprisk_adapter),
) -> OpportunityDetail:
    repo = OpportunityRepository(session, user.tenant_id)
    opportunity = await repo.get_or_404(opportunity_id)
    detail_error: dict[str, str] | None = None

    # 用 is not None 而非 bool()：后者不收缩类型，后面 fetch_detail([source_id]) 会报 Optional
    should_fetch = opportunity.source_id is not None and (
        opportunity.detail_status not in {"full", "attachment"}
        and (opportunity.detail_status != "failed" or refresh)
    )
    if should_fetch:
        # 详情拉取是付费调用：同一机会互斥，失败落 failed 终态阻断自动重拉，
        # 只有显式 refresh=true 才重试；记账失败不能让请求 500（重试会二次计费）。
        lock_key = f"opportunity-detail:{user.tenant_id}:{opportunity.id}"
        async with advisory_lock(lock_key) as acquired:
            if not acquired:
                detail_error = {
                    "code": "detail_in_progress",
                    "message": "详情正在拉取，请稍后刷新",
                }
            else:
                await session.refresh(opportunity)
                # source_id 非空由 should_fetch 保证；这里显式再判一次，
                # 既收缩类型供 fetch_detail 使用，也防 refresh 后数据变化。
                if opportunity.source_id is not None and opportunity.detail_status not in {
                    "full",
                    "attachment",
                }:
                    reservation = await reserve_paid_call(user.tenant_id)
                    try:
                        result = await adapter.fetch_detail([opportunity.source_id])
                    except Exception:
                        await release_paid_call(reservation)
                        raise
                    if not result.charged:
                        await release_paid_call(reservation)
                    try:
                        await record_gateway_call(
                            session,
                            tenant_id=user.tenant_id,
                            pcode="E_007_1271",
                            operation="opportunity.detail",
                            status=result.status.value,
                            order_no=result.order_no,
                            charged=result.charged,
                            rows=len(result.data or []),
                            query_hash_value=query_hash({"ids": [opportunity.source_id]}),
                            resource_type="opportunity",
                            resource_id=str(opportunity.id),
                            created_by=user.id,
                        )
                    except Exception:
                        logger.warning(
                            "opportunity detail bookkeeping failed", exc_info=True
                        )

                    if result.is_success and result.data:
                        item = next(
                            (
                                candidate
                                for candidate in result.data
                                if candidate.source_id == opportunity.source_id
                                or candidate.dedup_key == opportunity.dedup_key
                            ),
                            None,
                        )
                        if item is not None and item.detail_status in {"full", "attachment"}:
                            opportunity.detail_status = item.detail_status
                            if item.detail_status == "full":
                                opportunity.content_html = item.content_html
                                opportunity.content_text = item.content_text
                                opportunity.content_fetched_at = item.content_fetched_at
                            else:
                                # 附件原文：明确清空正文，避免把存储路径当正文展示
                                opportunity.content_html = None
                                opportunity.content_text = None
                                opportunity.content_fetched_at = None
                        else:
                            opportunity.detail_status = "failed"
                            detail_error = {
                                "code": "detail_not_found",
                                "message": "detail response did not contain the requested id",
                            }
                    else:
                        opportunity.detail_status = "failed"
                        error = result.error
                        detail_error = {
                            "code": error.code if error else result.status.value,
                            "message": error.message if error else result.status.value,
                        }
                        await AuditService(session).append(
                            tenant_id=user.tenant_id,
                            actor_id=user.id,
                            action="opportunity.detail_failed",
                            resource_type="opportunity",
                            resource_id=str(opportunity.id),
                            data=detail_error,
                        )
                    await session.commit()
                    await session.refresh(opportunity)

    awards = (
        await session.execute(
            AwardRepository(session, user.tenant_id)
            .scoped(select(AwardRecord).where(AwardRecord.opportunity_id == opportunity.id))
            .order_by(AwardRecord.award_at.desc().nullslast())
        )
    ).scalars().all()
    is_favorite = bool(
        (
            await session.execute(
                select(
                    exists().where(
                        OpportunityFavorite.tenant_id == user.tenant_id,
                        OpportunityFavorite.user_id == user.id,
                        OpportunityFavorite.opportunity_id == opportunity.id,
                    )
                )
            )
        ).scalar_one()
    )
    base = OpportunityListItem.model_validate(opportunity).model_dump()
    return OpportunityDetail(
        **base,
        content_html=opportunity.content_html,
        content_text=opportunity.content_text,
        content_fetched_at=opportunity.content_fetched_at,
        awards=[AwardResponse.model_validate(award) for award in awards],
        is_favorite=is_favorite,
        detail_error=detail_error,
    )


@router.post("/{opportunity_id}/favorite", response_model=FavoriteResponse)
async def favorite_opportunity(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> FavoriteResponse:
    await OpportunityRepository(session, user.tenant_id).get_or_404(opportunity_id)
    await session.execute(
        insert(OpportunityFavorite)
        .values(tenant_id=user.tenant_id, user_id=user.id, opportunity_id=opportunity_id)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "user_id", "opportunity_id"],
        )
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="opportunity.favorite",
        resource_type="opportunity",
        resource_id=str(opportunity_id),
        data={"favorited": True},
    )
    await session.commit()
    return FavoriteResponse(favorited=True)


@router.delete("/{opportunity_id}/favorite", response_model=FavoriteResponse)
async def unfavorite_opportunity(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> FavoriteResponse:
    await OpportunityRepository(session, user.tenant_id).get_or_404(opportunity_id)
    await session.execute(
        delete(OpportunityFavorite).where(
            OpportunityFavorite.tenant_id == user.tenant_id,
            OpportunityFavorite.user_id == user.id,
            OpportunityFavorite.opportunity_id == opportunity_id,
        )
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="opportunity.favorite",
        resource_type="opportunity",
        resource_id=str(opportunity_id),
        data={"favorited": False},
    )
    await session.commit()
    return FavoriteResponse(favorited=False)


@router.post("/sync", response_model=SyncRunResponse)
async def sync_opportunities(
    body: OpportunitySyncRequest,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskTenderAdapter = Depends(get_keeprisk_adapter),
) -> SyncRunResponse:
    # 同一查询的并发触发（双开标签页/双击）会重复发起付费调用；
    # 按租户+查询哈希互斥，不同查询不受影响。锁只覆盖付费段。
    lock_key = f"opportunity-sync:{user.tenant_id}:{query_hash(body.model_dump(mode='json'))}"
    async with advisory_lock(lock_key) as acquired:
        if not acquired:
            raise ConflictError("相同查询的同步正在执行中，请稍后重试")
        run = SourceSyncRun(
            tenant_id=user.tenant_id,
            source="keeprisk:E_007_1271",
            pcode="E_007_1271",
            query_json=body.model_dump(mode="json"),
            status="queued",
            created_by=user.id,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        run = await OpportunitySyncService(session, adapter).run(run.id)
    # 响应在记账前构建：rollback 会过期会话内实例（同 buyer-intel 路径）。
    response = SyncRunResponse.model_validate(run)
    # 付费调用已完成；记账失败不能让请求 500——重试会对同一查询二次计费。
    try:
        await record_gateway_call(
            session,
            tenant_id=user.tenant_id,
            pcode="E_007_1271",
            operation="opportunity.list",
            status=run.source_status or run.status,
            order_no=run.gateway_order_no,
            charged=run.charged,
            rows=run.rows_fetched,
            query_hash_value=query_hash(run.query_json),
            resource_type="source_sync_run",
            resource_id=str(run.id),
            created_by=user.id,
        )
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="opportunity.sync",
            resource_type="source_sync_run",
            resource_id=str(run.id),
            data={"status": run.status, "rows": run.rows_upserted},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("opportunity sync bookkeeping failed for run %s", response.id)
    if response.status == "succeeded":
        try:
            await run_auto_match_for_tenant(
                session,
                tenant_id=user.tenant_id,
                user_id=user.id,
                actor_id=user.id,
                limit=50,
            )
        except Exception:
            await session.rollback()
            logger.exception("opportunity sync auto match failed for run %s", response.id)
    return response


@router.get("/sync/{run_id}", response_model=SyncRunResponse)
async def get_sync(
    run_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> SyncRunResponse:
    run = await get_sync_run(session, user.tenant_id, run_id)
    if run is None:
        raise NotFoundError()
    return SyncRunResponse.model_validate(run)


@router.get("/{opportunity_id}/buyer-intel", response_model=OpportunityBuyerIntelResponse)
async def read_buyer_intel(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:read")),
    session: AsyncSession = Depends(get_session),
) -> OpportunityBuyerIntelResponse:
    await OpportunityRepository(session, user.tenant_id).get_or_404(opportunity_id)
    row = await get_buyer_intel(session, tenant_id=user.tenant_id, opportunity_id=opportunity_id)
    if row is None:
        raise NotFoundError()
    return OpportunityBuyerIntelResponse.model_validate(row)


@router.post("/{opportunity_id}/buyer-intel/sync", response_model=OpportunityBuyerIntelResponse)
async def run_buyer_intel(
    opportunity_id: UUID,
    user: CurrentUser = Depends(require_permission("opportunity:sync")),
    session: AsyncSession = Depends(get_session),
    adapter: KeepRiskCompanyIntelAdapter = Depends(get_company_intel_adapter),
) -> OpportunityBuyerIntelResponse:
    opportunity = await OpportunityRepository(session, user.tenant_id).get_or_404(opportunity_id)
    if not (opportunity.buyer or "").strip():
        raise ValidationAppError("该机会未披露采购人，无法查询采购人历史中标")
    # 按租户+机会互斥：并发查询同一采购人会重复付费；锁只覆盖付费调用。
    lock_key = f"opportunity-buyer-intel:{user.tenant_id}:{opportunity_id}"
    async with advisory_lock(lock_key) as acquired:
        if not acquired:
            raise ConflictError("该采购人的情报正在同步中，请稍后重试")
        row = await sync_buyer_intel(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            opportunity=opportunity,
            adapter=adapter,
        )
    # 响应在记账前构建：rollback 会过期会话内实例，async 下再同步取属性会抛
    # MissingGreenlet；且响应本就只应反映已提交的付费调用结果本身。
    response = OpportunityBuyerIntelResponse.model_validate(row)
    # 付费调用已在 sync_buyer_intel 内提交；记账失败不能让请求 500——
    # 否则用户重试会对同一查询二次计费。回滚记日志，与订阅调度路径同口径。
    try:
        await record_gateway_call(
            session,
            tenant_id=user.tenant_id,
            pcode="E_007_1271",
            operation="opportunity.buyer_intel",
            status=row.status,
            order_no=row.gateway_order_no,
            charged=row.charged,
            rows=row.rows_fetched,
            query_hash_value=query_hash({"buyer": row.buyer_name, "role": "tenderer"}),
            resource_type="opportunity",
            resource_id=str(opportunity.id),
            created_by=user.id,
        )
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="opportunity.buyer_intel_sync",
            resource_type="opportunity",
            resource_id=str(opportunity.id),
            data={
                "status": row.status,
                "rows": row.rows_fetched,
                "recent_wins_12m": row.recent_wins_12m,
                "existing_suppliers": len(row.existing_suppliers),
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("buyer intel bookkeeping failed for opportunity %s", opportunity_id)
    return response
