"""投标决策「证据链」：用本租户真实历史数据回答「这一单投不投」。

设计约束（见 AGENTS.md）：
- 纯确定性聚合：不调用 LLM、不访问外部网关、不写库、不消耗计量。
- 所有查询都带 tenant_id；跨租户按项目 ID 读取由 ``get_project`` 抛 404。
- **不编造数值**：数据不足时返回 ``scored=False`` + 明确 reason，绝不用 0 或
  默认值冒充统计结论；中位数/均值等统计量只在样本达标时输出。
- 每段证据都带取数口径：时间窗、条数上限、匹配方式、来源表名。

口径说明（buyer 匹配）：
1. 精确匹配（默认口径）：采购人名称经 NFKC + 去空白 + casefold 后完全相等。
   只有精确匹配的样本才参与评分。
2. 模糊匹配（仅提示）：精确匹配样本不足时，用包含关系再取一次，结果放在
   ``hint`` 字段并 ``scored=False``——中文采购人简称差异很大，模糊命中不能
   当作统计结论。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.opportunities.models import AwardRecord, Opportunity, OpportunityBuyerIntel
from app.modules.opportunities.repository import (
    AwardRepository,
    BuyerIntelRepository,
    OpportunityRepository,
)
from app.modules.pricing.price_band import percentile
from app.modules.projects.models import BidProject
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.service import get_latest_constraint, get_project

EVIDENCE_VERSION = "decision-evidence-v1"

# 取数窗口与样本下限：窗口 2 年覆盖大多数采购人的一个完整预算周期；
# 下限 3 条是最小可解释样本（少于 3 条时中位数/供应商数没有决策意义）。
AWARD_WINDOW_DAYS = 730
MIN_BUYER_AWARD_SAMPLES = 3
MIN_COMPETITOR_RECORDS = 3
MIN_OUR_DECIDED_SAMPLES = 3
MIN_BUDGET_ANCHOR_SAMPLES = 3
# 单次最多采样 500 条：按 award_at 倒序取最近记录，保证确定性与响应时间。
AWARD_SAMPLE_LIMIT = 500
# 模糊匹配的最短名称长度：过短的名称（如「医院」）包含匹配会命中一切。
MIN_FUZZY_BUYER_NAME_LENGTH = 4

BUYER_MATCH_EXACT = "exact"
BUYER_MATCH_FUZZY = "fuzzy"
BUYER_MATCH_NONE = "none"

SOURCE_AWARD_RECORDS = "award_records"
SOURCE_OPPORTUNITIES = "opportunities"
SOURCE_BUYER_INTEL = "opportunity_buyer_intel"
SOURCE_BID_PROJECTS = "bid_projects"
SOURCE_TENDER_CONSTRAINT = "tender_constraints"

DIMENSION_SOURCE_AWARD = "award-records"
DIMENSION_SOURCE_BUYER_INTEL = "buyer-intel"

# 竞争情报维度打分常量：这是启发式排序，不是中标概率预测。
# 每多一家历史中标供应商扣 12 分（第 2 家起），下限 30 分；
# 有我方胜率时把它作为 40% 的权重并入，避免只看对手数量忽略自身位置。
COMPETITION_SCORE_PER_EXTRA_SUPPLIER = 12.0
COMPETITION_SCORE_FLOOR = 30.0
OUR_WIN_RATE_BLEND = 0.4

ITEM_LABELS = {
    "buyer_history": "采购人历史中标",
    "competition": "竞争强度",
    "our_win_rate": "我方历史胜率",
    "budget_anchor": "预算锚点",
}


# ---------------------------------------------------------------------------
# 纯函数：可单测、无 IO
# ---------------------------------------------------------------------------


def normalize_name_key(name: str | None) -> str:
    """NFKC + 去全部空白 + casefold：同一采购人/厂商的全半角、大小写、空格差异合并。

    与 SQL 侧 ``buyer_sql_key`` 的口径必须一致（见该函数注释），否则精确匹配
    会因两侧归一化不同而漏样本。
    """
    if not name:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).casefold())


def buyer_sql_key(column: Any) -> Any:
    """SQL 侧的采购人归一化：小写 + 去掉全部空白。

    PostgreSQL 的 ``\\s`` 与 Python ``re`` 的 ``\\s`` 在本场景（中英文名称）等价：
    都覆盖空格、制表符、换行与全角空格。NFKC（全半角）只在 Python 侧做——
    库里若存了全角名称，精确匹配会落空并转入「模糊匹配仅提示」路径，
    宁可少算也不拿错数据充数。
    """
    return func.regexp_replace(func.lower(column), r"\s+", "", "g")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal | None) -> str | None:
    """金额统一以字符串出参，避免 float 往返误差。"""
    return f"{value:.2f}" if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class AwardSample:
    """一条中标记录的最小投影（只读，来自 award_records + opportunities.buyer）。"""

    winner: str
    win_amount: Decimal | None
    award_at: datetime | None
    project_name: str | None = None


def _item(
    key: str,
    *,
    scored: bool,
    reason: str,
    sources: list[str],
    scope: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    hint: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": ITEM_LABELS[key],
        "scored": scored,
        "reason": reason,
        "sources": sources,
        "scope": scope,
        "metrics": metrics or {},
        "hint": hint,
        "notes": notes or [],
    }


def _winner_tally(samples: list[AwardSample], limit: int = 5) -> list[dict[str, Any]]:
    tally: dict[str, dict[str, Any]] = {}
    for sample in samples:
        display = (sample.winner or "").strip()
        if not display:
            continue
        key = normalize_name_key(display)
        bucket = tally.setdefault(
            key,
            {
                "winner": display,
                "wins": 0,
                "latest_award_at": None,
                "total_amount": Decimal("0"),
            },
        )
        bucket["wins"] += 1
        if sample.win_amount is not None:
            bucket["total_amount"] += sample.win_amount
        if sample.award_at is not None and (
            bucket["latest_award_at"] is None or sample.award_at > bucket["latest_award_at"]
        ):
            bucket["latest_award_at"] = sample.award_at
    ordered = sorted(tally.values(), key=lambda row: (-row["wins"], row["winner"]))
    return [
        {
            "winner": row["winner"],
            "wins": int(row["wins"]),
            "latest_award_at": _iso(row["latest_award_at"]),
            "total_amount": _money(row["total_amount"] or None),
        }
        for row in ordered[:limit]
    ]


def build_buyer_history(
    samples: list[AwardSample],
    *,
    tenant_id: UUID | None,
    buyer: str | None,
    buyer_key: str,
    match_mode: str,
    record_count: int,
    truncated: bool,
    window_days: int = AWARD_WINDOW_DAYS,
    min_samples: int = MIN_BUYER_AWARD_SAMPLES,
) -> dict[str, Any]:
    """采购人历史中标：记录数、中标价中位数/均值、最近中标时间。

    样本未达标时只回真实计数（record_count 等），不输出中位数/均值——
    一条记录也能算出「中位数」，但那不是统计结论。
    """
    scope = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "buyer": buyer,
        "buyer_key": buyer_key or None,
        "match_mode": match_mode,
        "window_days": window_days,
        "sample_limit": AWARD_SAMPLE_LIMIT,
        "record_count": record_count,
        "truncated": truncated,
        "sources": [SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES],
    }
    amounts = sorted(
        sample.win_amount
        for sample in samples
        if sample.win_amount is not None and sample.win_amount > 0
    )
    award_dates = [sample.award_at for sample in samples if sample.award_at is not None]
    distinct_winners = len(
        {normalize_name_key(sample.winner) for sample in samples if sample.winner}
    )
    metrics: dict[str, Any] = {
        "record_count": record_count,
        "amount_samples": len(amounts),
        "distinct_winners": distinct_winners,
        "latest_award_at": _iso(max(award_dates)) if award_dates else None,
    }
    notes: list[str] = []
    if truncated:
        notes.append(
            f"命中记录超过单次采样上限 {AWARD_SAMPLE_LIMIT} 条，统计仅基于最近 {len(samples)} 条"
        )

    if not buyer:
        return _item(
            "buyer_history",
            scored=False,
            reason="项目未记录采购人，无法关联历史中标记录",
            sources=[SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES],
            scope=scope,
            metrics=metrics,
            notes=notes,
        )

    if match_mode == BUYER_MATCH_FUZZY:
        hint = {
            "match_mode": BUYER_MATCH_FUZZY,
            "record_count": record_count,
            "distinct_winners": metrics["distinct_winners"],
            "latest_award_at": metrics["latest_award_at"],
            "note": "模糊匹配仅作提示，未计入评分",
        }
        return _item(
            "buyer_history",
            scored=False,
            reason=(
                f"采购人「{buyer}」精确匹配的中标记录不足（当前 {record_count} 条，"
                f"需 ≥{min_samples} 条）；模糊匹配仅作提示，未计入评分"
            ),
            sources=[SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES],
            scope=scope,
            metrics=metrics,
            hint=hint,
            notes=notes,
        )

    if len(samples) < min_samples:
        return _item(
            "buyer_history",
            scored=False,
            reason=(
                f"采购人「{buyer}」{window_days} 天内仅 {len(samples)} 条中标记录"
                f"（需 ≥{min_samples} 条），样本不足，不输出中标价统计"
            ),
            sources=[SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES],
            scope=scope,
            metrics=metrics,
            notes=notes,
        )

    median = percentile(amounts, Decimal("0.50"))
    metrics.update(
        {
            "median_win_amount": _money(median),
            "p25_win_amount": _money(percentile(amounts, Decimal("0.25"))),
            "p75_win_amount": _money(percentile(amounts, Decimal("0.75"))),
            "mean_win_amount": _money(sum(amounts, Decimal("0")) / Decimal(len(amounts))),
            "top_winners": _winner_tally(samples),
        }
    )
    return _item(
        "buyer_history",
        scored=True,
        reason=(
            f"采购人「{buyer}」精确匹配：{window_days} 天窗口内 {len(samples)} 条中标记录，"
            f"中标价中位数 {_money(median)} 元（{len(amounts)} 条含金额）"
        ),
        sources=[SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES],
        scope=scope,
        metrics=metrics,
        notes=notes,
    )


def build_competition(
    samples: list[AwardSample],
    *,
    tenant_id: UUID | None,
    buyer: str | None,
    match_mode: str,
    record_count: int,
    buyer_intel: dict[str, Any] | None,
    window_days: int = AWARD_WINDOW_DAYS,
    min_records: int = MIN_COMPETITOR_RECORDS,
) -> dict[str, Any]:
    """竞争强度：同采购人历史中标供应商家数 + 采购人情报（若有）。

    判定标准是「记录条数达标 + 至少 2 家不同供应商」：只有 3 条记录但全是
    同一家，那说明存在强势在位供应商，本身就是强信号，可以评分；只有 1 家
    供应商的结论没有竞争含义，不评分。
    """
    sources = [SOURCE_AWARD_RECORDS, SOURCE_OPPORTUNITIES]
    if buyer_intel is not None:
        sources.append(SOURCE_BUYER_INTEL)
    scope = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "buyer": buyer,
        "match_mode": match_mode,
        "window_days": window_days,
        "sample_limit": AWARD_SAMPLE_LIMIT,
        "record_count": record_count,
        "sources": sources,
    }
    tally = _winner_tally(samples, limit=5)
    distinct_winners = len(
        {normalize_name_key(sample.winner) for sample in samples if sample.winner}
    )
    metrics: dict[str, Any] = {
        "distinct_winners": distinct_winners,
        "top_winners": tally,
    }
    if buyer_intel is not None:
        # DataResult.status 原样透传：ok/empty/unauthorized/... 不二值化。
        metrics["buyer_intel"] = buyer_intel
    if not buyer:
        return _item(
            "competition",
            scored=False,
            reason="项目未记录采购人，无法统计同采购人历史中标供应商",
            sources=sources,
            scope=scope,
            metrics=metrics,
        )
    if match_mode != BUYER_MATCH_EXACT:
        return _item(
            "competition",
            scored=False,
            reason="采购人精确匹配样本不足，竞争强度不评分（模糊匹配仅作提示）",
            sources=sources,
            scope=scope,
            metrics=metrics,
        )
    if len(samples) < min_records:
        return _item(
            "competition",
            scored=False,
            reason=(
                f"同采购人中标记录仅 {len(samples)} 条（需 ≥{min_records} 条），"
                "样本不足，不输出竞争强度结论"
            ),
            sources=sources,
            scope=scope,
            metrics=metrics,
        )
    if distinct_winners < 2:
        return _item(
            "competition",
            scored=False,
            reason=(
                f"同采购人 {len(samples)} 条中标记录全部由同一家供应商中标，"
                "无法评估竞争格局（仅作在位供应商提示）"
            ),
            sources=sources,
            scope=scope,
            metrics=metrics,
            hint={"match_mode": match_mode, "distinct_winners": distinct_winners},
        )
    return _item(
        "competition",
        scored=True,
        reason=(
            f"同采购人 {window_days} 天窗口内出现 {distinct_winners} 家"
            f" 中标供应商，共 {len(samples)} 条中标记录"
        ),
        sources=sources,
        scope=scope,
        metrics=metrics,
    )


def build_our_win_rate(
    *,
    tenant_id: UUID | None,
    won: int,
    lost: int,
    buyer: str | None,
    scope_label: str,
    min_samples: int = MIN_OUR_DECIDED_SAMPLES,
) -> dict[str, Any]:
    """我方历史胜率：只认项目终态（won/lost），不把在投项目当分母。"""
    decided = won + lost
    scope = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "scope": scope_label,
        "buyer": buyer,
        "window_days": None,
        "window_note": "项目状态为终态，统计全量历史，不设时间窗",
        "sources": [SOURCE_BID_PROJECTS],
    }
    metrics: dict[str, Any] = {"won": won, "lost": lost, "decided": decided}
    if decided < min_samples:
        return _item(
            "our_win_rate",
            scored=False,
            reason=(
                f"{scope_label}已决标项目仅 {decided} 个（需 ≥{min_samples} 个），"
                "样本不足，不输出胜率"
            ),
            sources=[SOURCE_BID_PROJECTS],
            scope=scope,
            metrics=metrics,
        )
    rate = Decimal(won) / Decimal(decided)
    metrics["win_rate"] = round(float(rate), 4)
    return _item(
        "our_win_rate",
        scored=True,
        reason=(
            f"{scope_label}已决标 {decided} 个（中标 {won} / 未中标 {lost}），"
            f"历史胜率 {rate:.1%}"
        ),
        sources=[SOURCE_BID_PROJECTS],
        scope=scope,
        metrics=metrics,
    )


def build_budget_anchor(
    *,
    tenant_id: UUID | None,
    buyer: str | None,
    budget: Decimal | None,
    budget_basis: str | None,
    budget_label: str | None,
    samples: list[AwardSample],
    window_days: int = AWARD_WINDOW_DAYS,
    min_samples: int = MIN_BUDGET_ANCHOR_SAMPLES,
) -> dict[str, Any]:
    """预算锚点：本项目预算 vs 采购人历史中标价中位数（含 P25/P50/P75 区间）。"""
    amounts = sorted(
        sample.win_amount
        for sample in samples
        if sample.win_amount is not None and sample.win_amount > 0
    )
    sources = [SOURCE_AWARD_RECORDS]
    if budget_basis == SOURCE_TENDER_CONSTRAINT:
        sources.append(SOURCE_TENDER_CONSTRAINT)
    else:
        sources.append(SOURCE_OPPORTUNITIES)
    scope = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "buyer": buyer,
        "budget_basis": budget_basis,
        "budget_label": budget_label,
        "window_days": window_days,
        "sample_limit": AWARD_SAMPLE_LIMIT,
        "sources": sources,
    }
    metrics: dict[str, Any] = {
        "amount_samples": len(amounts),
        "project_budget": _money(budget),
    }
    if budget is None or budget <= 0:
        return _item(
            "budget_anchor",
            scored=False,
            reason="未取到本项目预算/最高限价，无法与采购人历史中标价对齐",
            sources=sources,
            scope=scope,
            metrics=metrics,
        )
    if len(amounts) < min_samples:
        return _item(
            "budget_anchor",
            scored=False,
            reason=(
                f"采购人历史中标价样本不足（需 ≥{min_samples} 条，当前 {len(amounts)} 条），"
                "不输出预算锚点"
            ),
            sources=sources,
            scope=scope,
            metrics=metrics,
        )
    p25 = percentile(amounts, Decimal("0.25"))
    p50 = percentile(amounts, Decimal("0.50"))
    p75 = percentile(amounts, Decimal("0.75"))
    assert p25 is not None and p50 is not None and p75 is not None  # 样本已达标
    ratio = budget / p50
    if budget >= p75:
        position = "本项目预算高于历史 P75，价格空间偏宽（仍受最高限价约束）"
    elif budget >= p50:
        position = "本项目预算位于历史 P50～P75"
    elif budget >= p25:
        position = "本项目预算位于历史 P25～P50，报价空间偏紧"
    else:
        position = "本项目预算低于历史 P25，价格竞争充分，需先压成本"
    metrics.update(
        {
            "p25_win_amount": _money(p25),
            "median_win_amount": _money(p50),
            "p75_win_amount": _money(p75),
            "budget_to_median_ratio": f"{ratio:.4f}",
            "position": position,
        }
    )
    return _item(
        "budget_anchor",
        scored=True,
        reason=(
            f"本项目预算 {budget:.2f} 元（{budget_label or budget_basis}），"
            f"采购人历史中标价中位数 {p50:.2f} 元，预算/中位比 {ratio:.2f}；"
            f"历史样本 {len(amounts)} 条（{window_days} 天窗口）。{position}"
        ),
        sources=sources,
        scope=scope,
        metrics=metrics,
    )


def competition_signal(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把四段证据折成一个决策卡维度（key=competition）。

    返回 ``{"scored", "score", "reason", "source"}``；数据不足时 score=None，
    调用方必须把该维度排除在加权之外，不能拿默认分补位。
    """
    history = items.get("buyer_history") or {}
    competition = items.get("competition") or {}
    win_rate = items.get("our_win_rate") or {}
    anchor = items.get("budget_anchor") or {}

    if not competition.get("scored"):
        reason = (
            competition.get("reason")
            or history.get("reason")
            or "竞争情报样本不足，本维度不计分"
        )
        return {"scored": False, "score": None, "reason": reason, "source": DIMENSION_SOURCE_AWARD}

    distinct = int(competition["metrics"].get("distinct_winners") or 0)
    base = max(
        COMPETITION_SCORE_FLOOR,
        100.0 - COMPETITION_SCORE_PER_EXTRA_SUPPLIER * max(0, distinct - 1),
    )
    parts = [
        f"同采购人历史中标供应商 {distinct} 家"
        f"（{competition['scope'].get('window_days')} 天窗口，"
        f"{competition['scope'].get('record_count')} 条中标记录）"
    ]
    score = base
    if win_rate.get("scored"):
        rate = float(win_rate["metrics"].get("win_rate") or 0.0)
        score = base * (1.0 - OUR_WIN_RATE_BLEND) + rate * 100.0 * OUR_WIN_RATE_BLEND
        parts.append(
            f"我方同采购人胜率 {rate:.1%}"
            f"（{win_rate['metrics'].get('won')}/{win_rate['metrics'].get('decided')}）"
        )
    else:
        parts.append("我方同采购人已决标样本不足，胜率未参与加权")
    if anchor.get("scored"):
        parts.append(f"预算/历史中标价中位比 {anchor['metrics'].get('budget_to_median_ratio')}")
    intel = competition["metrics"].get("buyer_intel") if competition.get("metrics") else None
    if intel:
        parts.append(
            f"采购人情报近 12 个月中标 {intel.get('recent_wins_12m')} 条"
            f"（同步状态 {intel.get('status')}）"
        )
    parts.append("启发式排序，非中标概率预测")
    return {
        "scored": True,
        "score": round(min(100.0, max(0.0, score)), 2),
        "reason": "；".join(parts),
        "source": DIMENSION_SOURCE_AWARD,
    }


# ---------------------------------------------------------------------------
# SQL 构造：抽出来便于单测断言租户过滤（不连库）
# ---------------------------------------------------------------------------


def build_award_query(
    tenant_id: UUID,
    *,
    buyer_key: str | None,
    match_mode: str,
    since: datetime,
    limit: int = AWARD_SAMPLE_LIMIT,
) -> Select[Any]:
    """中标记录查询（award_records JOIN opportunities）。

    两张表都显式带 tenant_id 条件——join 出来的采购人名称属于另一个租户时
    不能参与本租户统计。
    """
    statement = (
        select(
            AwardRecord.winner,
            AwardRecord.win_amount,
            AwardRecord.award_at,
            AwardRecord.project_name,
        )
        .join(Opportunity, Opportunity.id == AwardRecord.opportunity_id)
        .where(
            AwardRecord.tenant_id == tenant_id,
            Opportunity.tenant_id == tenant_id,
            AwardRecord.award_at.is_not(None),
            AwardRecord.award_at >= since,
        )
        .order_by(AwardRecord.award_at.desc().nullslast())
        .limit(limit)
    )
    if buyer_key and match_mode == BUYER_MATCH_EXACT:
        statement = statement.where(buyer_sql_key(Opportunity.buyer) == buyer_key)
    elif buyer_key and match_mode == BUYER_MATCH_FUZZY:
        statement = statement.where(
            buyer_sql_key(Opportunity.buyer).contains(buyer_key, autoescape=True)
        )
    else:
        # 没有采购人可匹配时不要退化成「全租户中标记录」——那会把无关项目
        # 的中标价当成该采购人的历史价。
        statement = statement.where(buyer_sql_key(Opportunity.buyer) == "")
    return statement


def build_award_count_query(
    tenant_id: UUID, *, buyer_key: str | None, match_mode: str, since: datetime
) -> Select[Any]:
    statement = (
        select(func.count())
        .select_from(AwardRecord)
        .join(Opportunity, Opportunity.id == AwardRecord.opportunity_id)
        .where(
            AwardRecord.tenant_id == tenant_id,
            Opportunity.tenant_id == tenant_id,
            AwardRecord.award_at.is_not(None),
            AwardRecord.award_at >= since,
        )
    )
    if buyer_key and match_mode == BUYER_MATCH_EXACT:
        statement = statement.where(buyer_sql_key(Opportunity.buyer) == buyer_key)
    elif buyer_key and match_mode == BUYER_MATCH_FUZZY:
        statement = statement.where(
            buyer_sql_key(Opportunity.buyer).contains(buyer_key, autoescape=True)
        )
    else:
        statement = statement.where(buyer_sql_key(Opportunity.buyer) == "")
    return statement


# ---------------------------------------------------------------------------
# 聚合入口
# ---------------------------------------------------------------------------


def _award_samples(rows: Any) -> list[AwardSample]:
    samples: list[AwardSample] = []
    for row in rows:
        winner = getattr(row, "winner", None)
        samples.append(
            AwardSample(
                winner=str(winner or ""),
                win_amount=_decimal(getattr(row, "win_amount", None)),
                award_at=getattr(row, "award_at", None),
                project_name=getattr(row, "project_name", None),
            )
        )
    return samples


async def _buyer_intel_payload(
    session: AsyncSession, tenant_id: UUID, project: BidProject
) -> dict[str, Any] | None:
    if project.opportunity_id is None:
        return None
    row = (
        await session.execute(
            BuyerIntelRepository(session, tenant_id)
            .scoped(select(OpportunityBuyerIntel))
            .where(OpportunityBuyerIntel.opportunity_id == project.opportunity_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "status": row.status,
        "recent_wins_12m": int(row.recent_wins_12m or 0),
        "top_winners_count": len(row.top_winners or []),
        "existing_suppliers_count": len(row.existing_suppliers or []),
        "rows_fetched": int(row.rows_fetched or 0),
        "synced_at": _iso(row.synced_at),
        "lookback_days": 365,
        "source_table": SOURCE_BUYER_INTEL,
    }


async def _our_win_rate(
    session: AsyncSession, tenant_id: UUID, project: BidProject, buyer: str | None
) -> dict[str, Any]:
    statement = (
        ProjectRepository(session, tenant_id)
        .scoped(select(BidProject.status, func.count()))
        .where(BidProject.status.in_(("won", "lost")))
    )
    scope_label = "全租户"
    if buyer:
        statement = statement.where(buyer_sql_key(BidProject.buyer) == normalize_name_key(buyer))
        scope_label = "同采购人"
    statement = statement.group_by(BidProject.status)
    rows = (await session.execute(statement)).all()
    counts = {str(row[0]): int(row[1]) for row in rows}
    return build_our_win_rate(
        tenant_id=tenant_id,
        won=counts.get("won", 0),
        lost=counts.get("lost", 0),
        buyer=buyer,
        scope_label=scope_label,
    )


def _project_budget(
    constraint: Any, opportunity: Any
) -> tuple[Decimal | None, str | None, str | None]:
    """预算来源优先级：本项目招标文件解析结果 > 关联商机上的预算字段。"""
    payload = (constraint.payload if constraint is not None else None) or {}
    budget = payload.get("budget") or {}
    amount = _decimal(budget.get("amount"))
    if amount is not None and amount > 0:
        return amount, SOURCE_TENDER_CONSTRAINT, budget.get("label")
    if opportunity is not None:
        opportunity_amount = _decimal(getattr(opportunity, "budget_amount", None))
        if opportunity_amount is not None and opportunity_amount > 0:
            return opportunity_amount, SOURCE_OPPORTUNITIES, None
    return None, None, None


async def collect_decision_evidence(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    now: datetime | None = None,
    project: BidProject | None = None,
) -> dict[str, Any]:
    """聚合「投不投」证据链。只读，不写库、不消耗计量。

    ``project`` 由决策卡内部传入可省一次查询；外部调用传 ``tenant_id`` 与
    ``project_id`` 即可，跨租户/不存在都由 ``get_project`` 抛 404。
    """
    resolved = project or await get_project(session, tenant_id, project_id)
    if resolved.tenant_id != tenant_id:  # 防御：绝不跨租户复用传入对象
        raise AssertionError("project tenant mismatch")
    generated_at = now or datetime.now(UTC)
    since = generated_at - timedelta(days=AWARD_WINDOW_DAYS)
    raw_buyer = (resolved.buyer or "").strip()
    buyer: str | None = raw_buyer or None
    buyer_key = normalize_name_key(buyer)

    match_mode = BUYER_MATCH_NONE
    record_count = 0
    samples: list[AwardSample] = []
    if buyer_key:
        exact_count = int(
            (
                await session.execute(
                    build_award_count_query(
                        tenant_id, buyer_key=buyer_key, match_mode=BUYER_MATCH_EXACT, since=since
                    )
                )
            ).scalar_one()
        )
        match_mode = BUYER_MATCH_EXACT
        record_count = exact_count
        if exact_count < MIN_BUYER_AWARD_SAMPLES and len(buyer_key) >= MIN_FUZZY_BUYER_NAME_LENGTH:
            fuzzy_count = int(
                (
                    await session.execute(
                        build_award_count_query(
                            tenant_id,
                            buyer_key=buyer_key,
                            match_mode=BUYER_MATCH_FUZZY,
                            since=since,
                        )
                    )
                ).scalar_one()
            )
            if fuzzy_count > exact_count:
                match_mode = BUYER_MATCH_FUZZY
                record_count = fuzzy_count
        rows = (
            await session.execute(
                AwardRepository(session, tenant_id)
                .scoped(
                    build_award_query(
                        tenant_id,
                        buyer_key=buyer_key,
                        match_mode=match_mode,
                        since=since,
                    )
                )
            )
        ).all()
        samples = _award_samples(rows)

    buyer_history = build_buyer_history(
        samples,
        tenant_id=tenant_id,
        buyer=buyer,
        buyer_key=buyer_key,
        match_mode=match_mode,
        record_count=record_count,
        truncated=len(samples) >= AWARD_SAMPLE_LIMIT,
    )

    intel = await _buyer_intel_payload(session, tenant_id, resolved)
    competition = build_competition(
        samples,
        tenant_id=tenant_id,
        buyer=buyer,
        match_mode=match_mode,
        record_count=record_count,
        buyer_intel=intel,
    )
    our_win_rate = await _our_win_rate(session, tenant_id, resolved, buyer)

    constraint = await get_latest_constraint(session, tenant_id, project_id)
    opportunity = None
    if resolved.opportunity_id is not None:
        opportunity = (
            await session.execute(
                OpportunityRepository(session, tenant_id)
                .scoped(select(Opportunity))
                .where(Opportunity.id == resolved.opportunity_id)
            )
        ).scalar_one_or_none()
    budget, budget_basis, budget_label = _project_budget(constraint, opportunity)
    budget_anchor = build_budget_anchor(
        tenant_id=tenant_id,
        buyer=buyer,
        budget=budget,
        budget_basis=budget_basis,
        budget_label=budget_label,
        samples=samples,
    )

    items = [buyer_history, competition, our_win_rate, budget_anchor]
    missing = [f"{item['label']}：{item['reason']}" for item in items if not item["scored"]]
    notes = [
        f"取数窗口 {AWARD_WINDOW_DAYS} 天（award_at），单次采样上限 {AWARD_SAMPLE_LIMIT} 条。",
        "采购人名称精确匹配 = NFKC + 去空白 + casefold 后完全相等；模糊匹配仅作提示。",
        "样本不足的项 scored=False 且不输出中位数/胜率等统计量，不参与决策卡加权。",
    ]
    return {
        "project_id": str(resolved.id),
        "project_name": resolved.name,
        "buyer": buyer,
        "buyer_match_mode": match_mode,
        "window_days": AWARD_WINDOW_DAYS,
        "min_samples": {
            "buyer_awards": MIN_BUYER_AWARD_SAMPLES,
            "competitor_records": MIN_COMPETITOR_RECORDS,
            "our_decided": MIN_OUR_DECIDED_SAMPLES,
            "budget_anchor": MIN_BUDGET_ANCHOR_SAMPLES,
        },
        "generated_at": generated_at.isoformat(),
        "engine_version": EVIDENCE_VERSION,
        "items": items,
        "missing": missing,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# API 出参模型（放在本模块，避免与并行开发共用 schemas.py 冲突）
# ---------------------------------------------------------------------------


class EvidenceItemResponse(BaseModel):
    key: str
    label: str
    scored: bool
    reason: str
    sources: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    hint: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)


class DecisionEvidenceResponse(BaseModel):
    project_id: UUID
    project_name: str
    buyer: str | None = None
    buyer_match_mode: str
    window_days: int
    min_samples: dict[str, int]
    generated_at: datetime
    engine_version: str
    items: list[EvidenceItemResponse]
    missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
