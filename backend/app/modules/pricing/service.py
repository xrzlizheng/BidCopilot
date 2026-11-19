from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.generation.llm_client import LLMClient
from app.modules.opportunities.models import AwardRecord, Opportunity
from app.modules.pricing.cost_model import (
    CostModel,
    LaborLine,
    build_cost_baseline,
    decimal_or_none,
    parse_cost_model,
)
from app.modules.pricing.engine import (
    DEFAULT_TARGET_MARGINS,
    ENGINE_VERSION,
    build_narrative_prompt,
    build_strategy_payload,
    price_band_payload,
    template_narrative,
)
from app.modules.pricing.models import PriceStrategy
from app.modules.pricing.price_band import DISCLAIMER, PriceBand, build_price_band, in_scope
from app.modules.pricing.repository import (
    PriceStrategyRepository,
    PricingAwardRepository,
    PricingBidDecisionRunRepository,
    PricingBidResultRepository,
    PricingOpportunityRepository,
    PricingProjectRepository,
    PricingSettingRepository,
)
from app.modules.pricing.schemas import PricingStrategyRequest
from app.modules.projects.models import BidDecisionRun, BidProject, BidResult
from app.modules.projects.service import get_latest_constraint, get_project
from app.modules.system.models import TenantSetting

PRICING_COST_MODEL_KEY = "pricing_cost_model"
DECISION_COST_MODEL_KEY = "decision_cost_model"
TARGET_MARGINS_KEY = "pricing_target_margins"
HISTORY_LIMIT = 2000
HISTORY_WINDOW_DAYS = 730


@dataclass
class PriceHistory:
    award_ratios: list[Decimal] = field(default_factory=list)
    samples_by_source: dict[str, int] = field(default_factory=dict)
    decided_outcomes: list[str] = field(default_factory=list)
    price_loss_count: int = 0
    price_loss_ratios: list[Decimal] = field(default_factory=list)


def _count_source(history: PriceHistory, source: str) -> None:
    history.samples_by_source[source] = history.samples_by_source.get(source, 0) + 1


async def _setting_value(
    session: AsyncSession, tenant_id: UUID, key: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            PricingSettingRepository(session, tenant_id)
            .scoped(select(TenantSetting))
            .where(TenantSetting.key == key)
        )
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return None
    return row.value


async def load_cost_model(session: AsyncSession, tenant_id: UUID) -> CostModel:
    raw = await _setting_value(session, tenant_id, PRICING_COST_MODEL_KEY)
    if raw is not None:
        return parse_cost_model(raw, source=f"tenant-setting:{PRICING_COST_MODEL_KEY}")
    fallback = await _setting_value(session, tenant_id, DECISION_COST_MODEL_KEY)
    if fallback is not None:
        # 复用决策卡的直接/间接/税费率口径；该键没有人力单价，缺人力部分会如实报缺。
        return parse_cost_model(fallback, source=f"tenant-setting:{DECISION_COST_MODEL_KEY}")
    return parse_cost_model({}, source="missing:tenant-setting")


async def load_target_margins(
    session: AsyncSession, tenant_id: UUID
) -> tuple[dict[str, Decimal], str, str | None]:
    raw = await _setting_value(session, tenant_id, TARGET_MARGINS_KEY)
    if raw is None:
        return dict(DEFAULT_TARGET_MARGINS), "tool-default", None
    parsed: dict[str, Decimal] = {}
    for key in ("conservative", "balanced", "aggressive"):
        value = decimal_or_none(raw.get(key))
        if value is None or value < 0 or value >= 1:
            return (
                dict(DEFAULT_TARGET_MARGINS),
                "tool-default",
                f"租户目标净利率配置 {TARGET_MARGINS_KEY} 缺少或非法字段 {key}，已使用工具默认档位",
            )
        parsed[key] = value
    return parsed, f"tenant-setting:{TARGET_MARGINS_KEY}", None


async def resolve_budget(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    override: Decimal | None,
) -> tuple[Decimal | None, str]:
    if override is not None:
        return override, "input"
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    payload = constraint.payload if constraint is not None and isinstance(constraint.payload, dict) else {}
    budget = payload.get("budget")
    amount = None
    if isinstance(budget, dict):
        amount = decimal_or_none(budget.get("amount"))
    elif budget is not None:
        amount = decimal_or_none(budget)
    if amount is None:
        amount = decimal_or_none(payload.get("budget_amount"))
    if amount is not None and amount > 0:
        return amount, "tender-constraint"
    return None, "missing"


async def resolve_industry(
    session: AsyncSession,
    tenant_id: UUID,
    project: BidProject,
    override: str | None,
) -> tuple[list[str], str]:
    if override and override.strip():
        return [override.strip()], "input"
    if project.opportunity_id is None:
        return [], "missing"
    opportunity = (
        await session.execute(
            PricingOpportunityRepository(session, tenant_id)
            .scoped(select(Opportunity))
            .where(Opportunity.id == project.opportunity_id)
        )
    ).scalar_one_or_none()
    if opportunity is None:
        return [], "missing"
    industries = [str(value).strip() for value in (opportunity.industry or []) if str(value).strip()]
    return industries, "opportunity" if industries else "missing"


def _latest_quote_from_runs(runs: list[BidDecisionRun]) -> Decimal | None:
    ordered = sorted(
        runs,
        key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for run in ordered:
        inputs = run.inputs_json if isinstance(run.inputs_json, dict) else {}
        quote = decimal_or_none(inputs.get("quote_amount"))
        if quote is not None and quote > 0:
            return quote
    return None


async def collect_price_history(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: BidProject,
    target_industries: list[str],
    target_budget: Decimal | None,
) -> PriceHistory:
    history = PriceHistory()
    if target_budget is None or not target_industries:
        return history

    results = list(
        (
            await session.execute(
                PricingBidResultRepository(session, tenant_id)
                .scoped(select(BidResult))
                .order_by(BidResult.recorded_at.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    project_ids = {row.project_id for row in results if row.project_id != project.id}
    projects: dict[UUID, BidProject] = {}
    if project_ids:
        project_rows = list(
            (
                await session.execute(
                    PricingProjectRepository(session, tenant_id)
                    .scoped(select(BidProject))
                    .where(BidProject.id.in_(project_ids))
                )
            )
            .scalars()
            .all()
        )
        projects = {
            row.id: row
            for row in project_rows
            if row.id != project.id
            and row.created_at
            and row.created_at >= datetime.now(UTC) - timedelta(days=HISTORY_WINDOW_DAYS)
        }

    opportunity_ids = {row.opportunity_id for row in projects.values() if row.opportunity_id}
    opportunities: dict[UUID, Opportunity] = {}
    if opportunity_ids:
        opportunity_rows = list(
            (
                await session.execute(
                    PricingOpportunityRepository(session, tenant_id)
                    .scoped(select(Opportunity))
                    .where(Opportunity.id.in_(opportunity_ids))
                )
            )
            .scalars()
            .all()
        )
        opportunities = {row.id: row for row in opportunity_rows}

    def _sample_key(project_row: BidProject, opportunity: Opportunity | None) -> str:
        return f"opp:{opportunity.id}" if opportunity is not None else f"project:{project_row.id}"

    seen_samples: set[str] = set()
    decided_projects: set[UUID] = set()
    loss_projects: list[UUID] = []
    for result in results:
        if result.project_id == project.id:
            continue
        project_row = projects.get(result.project_id)
        if project_row is None:
            continue
        opportunity = opportunities.get(project_row.opportunity_id) if project_row.opportunity_id else None
        sample_budget = opportunity.budget_amount if opportunity is not None else None
        sample_industries = list(opportunity.industry or []) if opportunity is not None else []
        if not in_scope(
            target_industries=target_industries,
            sample_industries=sample_industries,
            target_budget=target_budget,
            sample_budget=sample_budget,
        ):
            continue
        if result.outcome in {"won", "lost"} and result.project_id not in decided_projects:
            decided_projects.add(result.project_id)
            history.decided_outcomes.append(result.outcome)
        if result.outcome == "won" and result.win_amount is not None and sample_budget:
            key = _sample_key(project_row, opportunity)
            if key not in seen_samples:
                seen_samples.add(key)
                ratio = result.win_amount / sample_budget
                if ratio > 0:
                    history.award_ratios.append(ratio)
                    _count_source(history, "bid_result_won")
        if result.outcome == "lost" and result.loss_reason == "price":
            history.price_loss_count += 1
            loss_projects.append(result.project_id)

    if loss_projects:
        runs = list(
            (
                await session.execute(
                    PricingBidDecisionRunRepository(session, tenant_id)
                    .scoped(select(BidDecisionRun))
                    .where(BidDecisionRun.project_id.in_(loss_projects))
                )
            )
            .scalars()
            .all()
        )
        runs_by_project: dict[UUID, list[BidDecisionRun]] = {}
        for run in runs:
            runs_by_project.setdefault(run.project_id, []).append(run)
        for project_id in loss_projects:
            project_row = projects.get(project_id)
            if project_row is None or project_row.opportunity_id is None:
                continue
            opportunity = opportunities.get(project_row.opportunity_id)
            budget = opportunity.budget_amount if opportunity is not None else None
            quote = _latest_quote_from_runs(runs_by_project.get(project_id, []))
            if budget and quote is not None:
                history.price_loss_ratios.append(quote / budget)

    awards = list(
        (
            await session.execute(
                PricingAwardRepository(session, tenant_id)
                .scoped(select(AwardRecord))
                .order_by(AwardRecord.award_at.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    award_opp_ids = {row.opportunity_id for row in awards if row.opportunity_id}
    if award_opp_ids:
        award_opps = list(
            (
                await session.execute(
                    PricingOpportunityRepository(session, tenant_id)
                    .scoped(select(Opportunity))
                    .where(Opportunity.id.in_(award_opp_ids))
                )
            )
            .scalars()
            .all()
        )
        opportunities.update({row.id: row for row in award_opps})
    for award in awards:
        opportunity = opportunities.get(award.opportunity_id) if award.opportunity_id else None
        budget = opportunity.budget_amount if opportunity is not None else None
        industries = list(opportunity.industry or []) if opportunity is not None else []
        if not in_scope(
            target_industries=target_industries,
            sample_industries=industries,
            target_budget=target_budget,
            sample_budget=budget,
        ):
            continue
        if award.win_amount is None or not budget:
            continue
        key = f"opp:{opportunity.id}" if opportunity is not None else f"award:{award.id}"
        if key in seen_samples:
            continue
        seen_samples.add(key)
        ratio = award.win_amount / budget
        if ratio > 0:
            history.award_ratios.append(ratio)
            _count_source(history, "award_record")

    # 招采信息自身已带中标金额的机会记录，也作为价格带样本（去重后计入）。
    opportunity_rows = list(
        (
            await session.execute(
                PricingOpportunityRepository(session, tenant_id)
                .scoped(select(Opportunity))
                .where(Opportunity.win_bid_amount.is_not(None))
                .order_by(Opportunity.publish_at.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    for opportunity in opportunity_rows:
        if opportunity.id == project.opportunity_id:
            continue
        if opportunity.budget_amount is None or opportunity.win_bid_amount is None:
            continue
        if not in_scope(
            target_industries=target_industries,
            sample_industries=list(opportunity.industry or []),
            target_budget=target_budget,
            sample_budget=opportunity.budget_amount,
        ):
            continue
        key = f"opp:{opportunity.id}"
        if key in seen_samples:
            continue
        seen_samples.add(key)
        ratio = opportunity.win_bid_amount / opportunity.budget_amount
        if ratio > 0:
            history.award_ratios.append(ratio)
            _count_source(history, "opportunity_win_bid")

    return history


def _serialize_inputs(
    *,
    quote: Decimal | None,
    budget: Decimal | None,
    other_direct_cost: Decimal | None,
    labor_items: list[LaborLine],
    industry: str | None,
    include_narrative: bool,
) -> dict[str, Any]:
    return {
        "quote_amount": str(quote) if quote is not None else None,
        "budget_amount": str(budget) if budget is not None else None,
        "other_direct_cost": str(other_direct_cost) if other_direct_cost is not None else None,
        "labor_items": [{"role": item.role, "days": str(item.days)} for item in labor_items],
        "industry": industry,
        "include_narrative": include_narrative,
    }


async def _build_narrative(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
    llm_client: LLMClient | None,
) -> tuple[str, str]:
    template = template_narrative(payload)
    if llm_client is None or not llm_client.configured():
        return template, "template"
    try:
        text = await llm_client.complete(
            system=(
                "你是投标报价策略解释器。只能依据输入的 JSON 计算结果写说明；"
                "不得编造历史数据或竞争对手信息；必须标注这是启发式估计、非模型预测、非中标承诺。"
            ),
            user=build_narrative_prompt(payload),
        )
        try:
            await record_gateway_call(
                session,
                tenant_id=tenant_id,
                pcode="llm",
                operation="pricing.strategy_narrative",
                status="ok",
                order_no=None,
                charged=True,
                rows=1,
                query_hash_value=query_hash(
                    {"project_id": str(project_id), "engine_version": ENGINE_VERSION}
                ),
                resource_type="price_strategy",
                resource_id=str(project_id),
                created_by=user_id,
            )
        except Exception:
            # 生成成功但计费台账未落库时，不把未计费的 LLM 文本作为正式策略输出。
            await session.rollback()
            return template, "template_fallback_billing_error"
        return text.strip(), "llm"
    except Exception:
        try:
            await record_gateway_call(
                session,
                tenant_id=tenant_id,
                pcode="llm",
                operation="pricing.strategy_narrative",
                status="failed",
                order_no=None,
                charged=False,
                rows=0,
                query_hash_value=query_hash(
                    {"project_id": str(project_id), "engine_version": ENGINE_VERSION}
                ),
                resource_type="price_strategy",
                resource_id=str(project_id),
                created_by=user_id,
            )
        except Exception:
            await session.rollback()
        return template, "template_fallback"


async def generate_strategy(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    body: PricingStrategyRequest,
    llm_client: LLMClient | None = None,
) -> PriceStrategy:
    project = await get_project(session, tenant_id, project_id)
    inputs = body.inputs
    budget, budget_source = await resolve_budget(
        session, tenant_id, project_id, inputs.budget_amount
    )
    industries, industry_source = await resolve_industry(
        session, tenant_id, project, inputs.industry
    )
    model = await load_cost_model(session, tenant_id)
    target_margins, target_margins_source, target_margins_note = await load_target_margins(
        session, tenant_id
    )
    labor_lines = [
        LaborLine(role=item.role.strip(), days=item.days) for item in inputs.labor_items
    ]
    cost = build_cost_baseline(
        model,
        labor_items=labor_lines,
        other_direct_cost=inputs.other_direct_cost,
        quote=inputs.quote_amount,
    )
    history = await collect_price_history(
        session,
        tenant_id=tenant_id,
        project=project,
        target_industries=industries,
        target_budget=budget,
    )
    band = build_price_band(
        award_ratios=history.award_ratios,
        decided_outcomes=history.decided_outcomes,
        price_loss_count=history.price_loss_count,
        samples_by_source=history.samples_by_source,
    )
    result = build_strategy_payload(
        budget=budget,
        budget_source=budget_source,
        industry=industries,
        industry_source=industry_source,
        quote=inputs.quote_amount,
        cost=cost,
        band=band,
        indirect_rate=model.indirect_rate,
        tax_rate=model.tax_rate,
        target_margins=target_margins,
        target_margins_source=target_margins_source,
    )
    if history.price_loss_ratios:
        result["price_band"]["price_loss_quote_ratios"] = [
            format(value.quantize(Decimal("0.000001")), "f")
            for value in history.price_loss_ratios
        ]
    if target_margins_note:
        result["assumptions"]["target_margins_note"] = target_margins_note
    narrative = {"text": "", "source": "template", "disclaimer": DISCLAIMER}
    if inputs.include_narrative:
        text, source = await _build_narrative(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            payload=result,
            llm_client=llm_client,
        )
        narrative = {"text": text, "source": source, "disclaimer": DISCLAIMER}
    result["narrative"] = narrative

    row = PriceStrategy(
        tenant_id=tenant_id,
        project_id=project_id,
        inputs_json=_serialize_inputs(
            quote=inputs.quote_amount,
            budget=inputs.budget_amount,
            other_direct_cost=inputs.other_direct_cost,
            labor_items=labor_lines,
            industry=inputs.industry,
            include_narrative=inputs.include_narrative,
        ),
        result_json=result,
        engine_version=ENGINE_VERSION,
        created_by=user_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def serialize_strategy(row: PriceStrategy) -> dict[str, Any]:
    result = dict(row.result_json or {})
    result.update(
        {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "engine_version": row.engine_version,
            "created_by": str(row.created_by) if row.created_by else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "inputs": row.inputs_json or {},
        }
    )
    return result


async def latest_strategy(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> PriceStrategy:
    await get_project(session, tenant_id, project_id)
    row = (
        await session.execute(
            PriceStrategyRepository(session, tenant_id)
            .scoped(select(PriceStrategy))
            .where(PriceStrategy.project_id == project_id)
            .order_by(PriceStrategy.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("尚未生成报价策略")
    return row


async def price_band_for_project(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, Any]:
    project = await get_project(session, tenant_id, project_id)
    budget, budget_source = await resolve_budget(session, tenant_id, project_id, None)
    industries, industry_source = await resolve_industry(session, tenant_id, project, None)
    history = await collect_price_history(
        session,
        tenant_id=tenant_id,
        project=project,
        target_industries=industries,
        target_budget=budget,
    )
    band: PriceBand = build_price_band(
        award_ratios=history.award_ratios,
        decided_outcomes=history.decided_outcomes,
        price_loss_count=history.price_loss_count,
        samples_by_source=history.samples_by_source,
    )
    payload = price_band_payload(band, budget)
    return {
        "project_id": str(project_id),
        "status": payload["status"],
        "budget_amount": str(budget) if budget is not None else None,
        "budget_source": budget_source,
        "industry": industries,
        "industry_source": industry_source,
        "price_band": payload,
        "hypothesis": DISCLAIMER,
    }


async def sensitivity_for_project(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, Any]:
    row = await latest_strategy(session, tenant_id, project_id)
    result = row.result_json or {}
    return {
        "project_id": str(project_id),
        "engine_version": row.engine_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": (result.get("sensitivity") or {}).get("status", "insufficient_data"),
        "sensitivity": result.get("sensitivity"),
        "missing": result.get("missing") or [],
        "disclaimer": DISCLAIMER,
    }
