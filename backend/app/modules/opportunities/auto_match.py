"""机会自动匹配与自动甄别。

打分口径是**启发式规则**（订阅条件命中 + 企业能力/资质覆盖），不是模型评分，
也不代表中标概率。匹配结果持久化到 opportunity_matches，由订阅同步、批量任务
或单条接口触发重算；高匹配且资格全部通过的记录可按租户开关幂等自动立项。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.locks import advisory_lock
from app.db.repository import TenantRepository
from app.modules.assets.models import Company, Credit, Performance, Personnel, Qualification
from app.modules.assets.repository import AssetRepository, CompanyRepository
from app.modules.audit.service import AuditService
from app.modules.notifications.models import Notification
from app.modules.notifications.service import NotificationService
from app.modules.opportunities.matching import (
    MatchPreference,
    explain_opportunity,
    hit_groups,
    preferences_from_subscriptions,
)
from app.modules.opportunities.models import (
    Opportunity,
    OpportunityMatch,
    OpportunitySubscription,
)
from app.modules.opportunities.repository import (
    OpportunityMatchRepository,
    OpportunityRepository,
    SubscriptionRepository,
    TenantSettingRepository,
)
from app.modules.projects.models import BidProject, TenderConstraint
from app.modules.projects.repository import ProjectRepository
from app.modules.system.models import TenantSetting


class _TenderConstraintRepository(TenantRepository[TenderConstraint]):
    model = TenderConstraint

logger = logging.getLogger("bidcopilot.opportunities.auto_match")

AUTO_MATCH_SETTING_KEY = "opportunity_auto_match"
ENGINE_VERSION = "heuristic-rules-v1"

DEFAULT_AUTO_MATCH_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "recommend_min_score": 60,
    "skip_below_score": 30,
    "auto_create_project": False,
    "notify": True,
}

QUALIFICATION_SECTION_MARKERS = (
    "资格要求",
    "资格条件",
    "投标人资格",
    "供应商资格",
    "申请人资格",
    "响应人资格",
)
SECTION_STOP_MARKERS = (
    "评标办法",
    "评分办法",
    "评分标准",
    "技术要求",
    "商务要求",
    "采购需求",
    "项目概况",
    "投标文件",
    "响应文件",
    "开标",
    "合同",
    "获取招标文件",
    "公告期限",
)

LEVEL_RANK = {
    "特级": 6,
    "甲级": 5,
    "一级": 5,
    "A级": 5,
    "乙级": 4,
    "二级": 4,
    "B级": 4,
    "丙级": 3,
    "三级": 3,
    "C级": 3,
    "丁级": 2,
    "四级": 2,
}

CERT_KEYWORDS = (
    "信息系统集成",
    "信息安全",
    "安防",
    "建筑",
    "市政",
    "机电",
    "电子与智能化",
    "消防",
    "环保",
    "装饰",
    "CMMI",
    "ISO9001",
    "ISO27001",
    "ITSS",
    "CCRC",
    "CS",
)


@dataclass(frozen=True)
class CompanyCapability:
    company_id: UUID
    company_name: str
    region: str | None
    industry: str | None
    qualifications: tuple[dict[str, Any], ...] = ()
    personnel: tuple[dict[str, Any], ...] = ()
    performances: tuple[dict[str, Any], ...] = ()
    credits: tuple[dict[str, Any], ...] = ()


def _norm(value: str | None) -> str:
    return re.sub(r"[\s\W_]+", "", (value or "").casefold(), flags=re.UNICODE)


def _shared_text(left: str | None, right: str | None) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    for token in re.findall(r"[a-z0-9]{2,}", left_norm):
        if token in right_norm:
            return True
    for keyword in CERT_KEYWORDS:
        key = _norm(keyword)
        if key and key in left_norm and key in right_norm:
            return True
    for index in range(max(0, len(left_norm) - 3)):
        if left_norm[index : index + 4] in right_norm:
            return True
    return False


def _is_stop_heading(line: str) -> bool:
    compact = line.strip()
    if len(compact) > 40 or not any(marker in compact for marker in SECTION_STOP_MARKERS):
        return False
    if compact.startswith(("评标", "评分", "技术", "商务", "采购需求", "投标文件", "响应文件")):
        return True
    return bool(re.match(r"^(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+[、.．]|\d+[、.．])", compact))


def _is_section_start(line: str) -> bool:
    compact = line.strip()
    if len(compact) > 60 or "详见" in compact:
        return False
    if any(marker in compact for marker in ("投标人资格", "供应商资格", "申请人资格", "响应人资格")):
        return True
    if not any(marker in compact for marker in ("资格要求", "资格条件")):
        return False
    return bool(
        re.match(r"^(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+[、.．]|\d+[、.．])", compact)
        or compact.startswith(("资格要求", "资格条件"))
        or compact.endswith(("：", ":"))
    )


def _strip_bullet(line: str) -> str:
    return re.sub(
        r"^\s*(?:\([一二三四五六七八九十\d]+\)|[一二三四五六七八九十]+[、.．)）]|\d+[、.．)）]|[-*•·])\s*",
        "",
        line,
    ).strip()


def classify_requirement(text: str) -> str:
    if any(keyword in text for keyword in ("业绩", "案例", "合同", "项目经验")):
        return "PERFORMANCE"
    if any(keyword in text for keyword in ("人员", "项目经理", "建造师", "工程师", "持证", "社保")):
        return "PERSONNEL"
    if any(keyword in text for keyword in ("信用", "失信", "违法", "重大税收", "黑名单")):
        return "CREDIT"
    if any(
        keyword in text
        for keyword in ("资质", "认证", "证书", "等级", "许可证", "ISO", "CMMI", "ITSS")
    ):
        return "QUALIFICATION"
    return "OTHER"


def detect_required_level(text: str) -> str | None:
    for level in LEVEL_RANK:
        if level in text:
            return level
    return None


def detect_quantity(text: str) -> int | None:
    match = re.search(r"(?:不少于|不低于|至少|≥)\s*(\d+)\s*(?:个|项|名|份|台|套)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*(?:个|项|名|份|台|套)", text)
    return int(match.group(1)) if match else None


def extract_qualification_requirements(content_text: str | None) -> list[dict[str, Any]]:
    """从公告正文的资格要求段落解析条款。

    只抽取正文明确写出的条款；找不到章节时返回空列表，由调用方标 UNKNOWN，
    不按标题/行业/预算猜测资格要求。
    """
    if not content_text or not content_text.strip():
        return []
    lines = [line.strip() for line in re.split(r"[\r\n]+", content_text) if line.strip()]
    collected: list[tuple[int, str]] = []
    collecting = False
    for index, line in enumerate(lines):
        if _is_section_start(line):
            collecting = True
            remainder = re.split(r"[:：]", line, maxsplit=1)
            if len(remainder) == 2 and len(remainder[1].strip()) >= 4:
                collected.append((index, remainder[1].strip()))
            continue
        if not collecting:
            continue
        if _is_stop_heading(line):
            break
        collected.append((index, line))
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in collected:
        for segment in re.split(r"[；;]\s*", _strip_bullet(raw)):
            text = segment.strip(" \t：:，,。.、")
            if len(text) < 4 or _is_stop_heading(text):
                continue
            key = _norm(text)
            if not key or key in seen:
                continue
            seen.add(key)
            requirements.append(
                {
                    "text": text,
                    "category": classify_requirement(text),
                    "level": detect_required_level(text),
                    "quantity": detect_quantity(text),
                    "source_span": f"line:{index}",
                }
            )
    return requirements


def normalize_requirements(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("name") or item.get("item") or "").strip()
        if len(text) < 4:
            continue
        key = _norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        raw_category = str(item.get("category") or "").upper()
        category = raw_category if raw_category in {
            "QUALIFICATION",
            "PERSONNEL",
            "PERFORMANCE",
            "CREDIT",
            "OTHER",
        } else classify_requirement(text)
        requirements.append(
            {
                "text": text,
                "category": category,
                "level": item.get("level") or detect_required_level(text),
                "quantity": item.get("quantity") or detect_quantity(text),
                "source_span": item.get("source_span") or "constraint",
            }
        )
    return requirements


def requirements_from_constraint(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("qualification_requirements")
    if not isinstance(raw, list):
        return []
    return normalize_requirements(raw)


async def load_constraint_requirements(
    session: AsyncSession, *, tenant_id: UUID, opportunity_id: UUID
) -> list[dict[str, Any]]:
    project = (
        await session.execute(
            ProjectRepository(session, tenant_id)
            .scoped(select(BidProject))
            .where(BidProject.opportunity_id == opportunity_id)
            .order_by(BidProject.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if project is None:
        return []
    constraint = (
        await session.execute(
            _TenderConstraintRepository(session, tenant_id)
            .scoped(select(TenderConstraint))
            .where(TenderConstraint.project_id == project.id)
            .order_by(TenderConstraint.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return requirements_from_constraint(constraint.payload if constraint is not None else None)


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _level_rank(level: str | None) -> int | None:
    if not level:
        return None
    for key, rank in LEVEL_RANK.items():
        if key in level:
            return rank
    return None


def _result(
    requirement: dict[str, Any],
    status: str,
    reason: str,
    *,
    matched_asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "requirement": requirement.get("text") or "",
        "category": requirement.get("category") or "OTHER",
        "status": status,
        "reason": reason,
        "level": requirement.get("level"),
        "quantity": requirement.get("quantity"),
        "matched_asset_ids": matched_asset_ids or [],
    }


def _match_requirement(
    requirement: dict[str, Any],
    capability: CompanyCapability,
    deadline: date | None,
) -> dict[str, Any]:
    category = requirement.get("category") or "OTHER"
    text = requirement.get("text") or ""
    if category == "QUALIFICATION":
        candidates = [
            asset for asset in capability.qualifications if _shared_text(asset.get("name"), text)
        ]
        if not candidates:
            return _result(requirement, "MISSING", "企业资质库未找到匹配项")
        asset = candidates[0]
        asset_id = str(asset.get("id") or "")
        expires = _parse_date(asset.get("valid_to"))
        if expires is None:
            return _result(
                requirement, "UNKNOWN", "证书有效期缺失，无法判定", matched_asset_ids=[asset_id]
            )
        if deadline is None:
            return _result(
                requirement,
                "UNKNOWN",
                "招标截止日未知，无法判定证书有效期是否覆盖",
                matched_asset_ids=[asset_id],
            )
        if expires < deadline:
            return _result(
                requirement, "FAIL", "证书已过期", matched_asset_ids=[asset_id]
            )
        required_rank = _level_rank(requirement.get("level"))
        actual_rank = _level_rank(asset.get("level"))
        if required_rank is not None and actual_rank is None:
            return _result(
                requirement, "UNKNOWN", "资质等级无法比较", matched_asset_ids=[asset_id]
            )
        if required_rank is not None and actual_rank is not None and actual_rank < required_rank:
            return _result(
                requirement, "FAIL", "资质等级不足", matched_asset_ids=[asset_id]
            )
        return _result(
            requirement, "PASS", "资质匹配且有效期覆盖", matched_asset_ids=[asset_id]
        )
    if category == "PERSONNEL":
        candidates = [
            asset
            for asset in capability.personnel
            if _shared_text(asset.get("cert_type"), text) or _shared_text(asset.get("name"), text)
        ]
        if not candidates:
            return _result(requirement, "MISSING", "企业人员库未找到匹配项")
        asset = candidates[0]
        asset_id = str(asset.get("id") or "")
        expires = _parse_date(asset.get("valid_to"))
        if expires is None:
            return _result(
                requirement, "UNKNOWN", "人员证书有效期缺失，无法判定", matched_asset_ids=[asset_id]
            )
        if deadline is None:
            return _result(
                requirement,
                "UNKNOWN",
                "招标截止日未知，无法判定人员证书有效期是否覆盖",
                matched_asset_ids=[asset_id],
            )
        if expires < deadline:
            return _result(
                requirement, "FAIL", "人员证书已过期", matched_asset_ids=[asset_id]
            )
        return _result(requirement, "PASS", "人员证书匹配且有效", matched_asset_ids=[asset_id])

    if category == "PERFORMANCE":
        quantity = int(requirement.get("quantity") or 1)
        candidates = list(capability.performances)
        if not candidates:
            return _result(requirement, "MISSING", "企业业绩库未找到匹配项")
        ids = [str(asset.get("id") or "") for asset in candidates]
        if len(candidates) < quantity:
            return _result(
                requirement,
                "FAIL",
                f"业绩数量不足（要求 {quantity}，找到 {len(candidates)}）",
                matched_asset_ids=ids,
            )
        return _result(requirement, "PASS", "业绩数量满足要求", matched_asset_ids=ids)

    if category == "CREDIT":
        candidates = [
            asset
            for asset in capability.credits
            if _shared_text(asset.get("check_item"), text) or _shared_text(asset.get("name"), text)
        ]
        if not candidates:
            return _result(requirement, "MISSING", "企业信用库未找到匹配记录")
        ids = [str(asset.get("id") or "") for asset in candidates]
        statuses = " ".join(str(asset.get("status") or "") for asset in candidates)
        if any(keyword in statuses for keyword in ("异常", "失信", "违法", "注销")):
            return _result(requirement, "FAIL", "信用记录存在异常", matched_asset_ids=ids)
        if "正常" in statuses:
            return _result(requirement, "PASS", "信用记录正常", matched_asset_ids=ids)
        return _result(requirement, "UNKNOWN", "信用状态无法判断", matched_asset_ids=ids)

    return _result(requirement, "UNKNOWN", "该要求类型暂不支持自动判定")


def evaluate_company_qualifications(
    requirements: list[dict[str, Any]],
    capability: CompanyCapability,
    # deadline 允许为 None：_match_requirement 对「截止日未知」有显式的 UNKNOWN 分支
    deadline: date | None,
) -> dict[str, Any]:
    results = [_match_requirement(requirement, capability, deadline) for requirement in requirements]
    gaps = [item for item in results if item["status"] != "PASS"]
    pass_count = len(results) - len(gaps)
    statuses = {item["status"] for item in gaps}
    if not requirements:
        qualified: bool | None = None
    elif statuses & {"MISSING", "FAIL"}:
        qualified = False
    elif "UNKNOWN" in statuses:
        qualified = None
    else:
        qualified = True
    return {
        "company_id": capability.company_id,
        "company_name": capability.company_name,
        "qualified": qualified,
        "pass_count": pass_count,
        "total": len(results),
        "unknown_count": sum(1 for item in gaps if item["status"] == "UNKNOWN"),
        "gaps": gaps,
        "results": results,
    }


def select_best_company_qualification(
    requirements: list[dict[str, Any]],
    capabilities: list[CompanyCapability],
    deadline: date | None,
) -> dict[str, Any]:
    if not requirements:
        return {
            "company_id": None,
            "company_name": None,
            "qualified": None,
            "pass_count": 0,
            "total": 0,
            "unknown_count": 0,
            "gaps": [
                {
                    "requirement": "资格要求",
                    "category": "OTHER",
                    "status": "UNKNOWN",
                    "reason": "未能从公告正文解析出资格要求，需人工复核",
                    "level": None,
                    "quantity": None,
                    "matched_asset_ids": [],
                }
            ],
            "results": [],
        }
    if not capabilities:
        return {
            "company_id": None,
            "company_name": None,
            "qualified": False,
            "pass_count": 0,
            "total": len(requirements),
            "unknown_count": 0,
            "gaps": [
                {
                    "requirement": item.get("text") or "",
                    "category": item.get("category") or "OTHER",
                    "status": "MISSING",
                    "reason": "租户尚未建立企业档案与资质库",
                    "level": item.get("level"),
                    "quantity": item.get("quantity"),
                    "matched_asset_ids": [],
                }
                for item in requirements
            ],
            "results": [],
        }
    evaluated = [
        evaluate_company_qualifications(requirements, capability, deadline)
        for capability in capabilities
    ]
    evaluated.sort(
        key=lambda item: (
            1 if item["qualified"] is True else 0,
            item["pass_count"],
            -item["unknown_count"],
        ),
        reverse=True,
    )
    return evaluated[0]


HIT_GROUP_WEIGHTS = {
    "keyword": 10,
    "region": 10,
    "industry": 10,
    "budget": 10,
    "company_region": 10,
    "company_industry": 20,
}


def company_capability_hit_groups(
    opportunity: Opportunity, capabilities: list[CompanyCapability]
) -> list[str]:
    groups: set[str] = set()
    region = (opportunity.region or "").casefold()
    industries = [item.casefold() for item in (opportunity.industry or []) if item]
    for capability in capabilities:
        company_region = (capability.region or "").casefold()
        if region and company_region and (company_region in region or region in company_region):
            groups.add("company_region")
        company_industry = (capability.industry or "").casefold()
        if company_industry and any(
            industry in company_industry or company_industry in industry for industry in industries
        ):
            groups.add("company_industry")
    return [name for name in ("company_region", "company_industry") if name in groups]


def evaluate_opportunity_match(
    opportunity: Opportunity,
    preferences: list[MatchPreference],
    qualification: dict[str, Any],
    capabilities: list[CompanyCapability],
) -> dict[str, Any]:
    """启发式规则打分：订阅命中 + 企业地域/行业能力 + 资质覆盖。

    分值是 0-100 的规则分，不是模型评分，也不是中标概率。
    """
    groups = hit_groups(preferences, opportunity)
    groups += company_capability_hit_groups(opportunity, capabilities)
    total = int(qualification.get("total") or 0)
    passed = int(qualification.get("pass_count") or 0)
    if total and passed:
        groups.append("qualification")
    score = sum(HIT_GROUP_WEIGHTS.get(group, 0) for group in set(groups))
    if total:
        score += round(30 * passed / total)
    score = max(0, min(100, score))

    explanation = explain_opportunity(preferences, opportunity)
    reasons = list(explanation.get("reasons") or [])
    risks = list(explanation.get("risks") or [])
    if total:
        if qualification.get("qualified") is True:
            reasons.append(f"资格自检通过（{passed}/{total}）")
        elif qualification.get("qualified") is False:
            risks.append(f"资格自检存在 {len(qualification.get('gaps') or [])} 项缺口")
        else:
            risks.append("资格自检存在无法判定项，需人工复核")
    else:
        risks.append("未解析到资格要求，资格状态 unknown")
    if "company_industry" in groups:
        reasons.append("企业行业方向与机会匹配")
    if "company_region" in groups:
        reasons.append("企业所在地区与机会匹配")
    unique_reasons = list(dict.fromkeys(item for item in reasons if item))
    unique_risks = list(dict.fromkeys(item for item in risks if item))
    return {
        "match_score": score,
        "hit_groups": list(dict.fromkeys(groups)),
        "reasons": unique_reasons[:8],
        "risks": unique_risks[:8],
    }


def decide_match(qualified: bool | None, score: int, settings: dict[str, Any]) -> str:
    if qualified is True and score >= int(settings["recommend_min_score"]):
        return "recommend"
    if score < int(settings["skip_below_score"]):
        return "skip"
    return "review"


def validate_auto_match_settings(
    value: dict[str, Any], base: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = dict(DEFAULT_AUTO_MATCH_SETTINGS)
    if base:
        merged.update({key: base[key] for key in DEFAULT_AUTO_MATCH_SETTINGS if key in base})
    merged.update(value)
    try:
        recommend = int(merged["recommend_min_score"])
        skip = int(merged["skip_below_score"])
    except (TypeError, ValueError) as exc:
        raise ValidationAppError("匹配阈值必须是 0-100 的整数") from exc
    if not 0 <= skip <= 100 or not 0 <= recommend <= 100:
        raise ValidationAppError("匹配阈值必须在 0-100 之间")
    if skip > recommend:
        raise ValidationAppError("skip_below_score 不得大于 recommend_min_score")
    return {
        "enabled": bool(merged["enabled"]),
        "recommend_min_score": recommend,
        "skip_below_score": skip,
        "auto_create_project": bool(merged["auto_create_project"]),
        "notify": bool(merged["notify"]),
    }


def project_creation_plan(
    *,
    decision: str,
    auto_create_project: bool,
    existing_project_id: UUID | None,
    lock_acquired: bool,
) -> str:
    """幂等立项分支：skip / existing / concurrent / create。"""
    if decision != "recommend" or not auto_create_project:
        return "skip"
    if existing_project_id is not None:
        return "existing"
    if not lock_acquired:
        return "concurrent"
    return "create"


async def get_auto_match_settings(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    repo = TenantSettingRepository(session, tenant_id)
    row = (
        await session.execute(
            repo.scoped(select(TenantSetting)).where(TenantSetting.key == AUTO_MATCH_SETTING_KEY)
        )
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        settings = validate_auto_match_settings({})
        settings["source"] = "default"
        return settings
    try:
        settings = validate_auto_match_settings(row.value)
    except ValidationAppError:
        logger.warning("tenant %s has invalid %s; using defaults", tenant_id, AUTO_MATCH_SETTING_KEY)
        settings = validate_auto_match_settings({})
        settings["source"] = "default"
        return settings
    settings["source"] = "tenant-setting"
    return settings


async def update_auto_match_settings(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    updates: dict[str, Any],
) -> dict[str, Any]:
    current = await get_auto_match_settings(session, tenant_id)
    merged = validate_auto_match_settings(updates, base=current)
    repo = TenantSettingRepository(session, tenant_id)
    row = (
        await session.execute(
            repo.scoped(select(TenantSetting)).where(TenantSetting.key == AUTO_MATCH_SETTING_KEY)
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            TenantSetting(
                tenant_id=tenant_id,
                key=AUTO_MATCH_SETTING_KEY,
                value=merged,
                updated_by=user_id,
            )
        )
    else:
        row.value = merged
        row.updated_by = user_id
    await session.commit()
    return await get_auto_match_settings(session, tenant_id)


async def load_company_capabilities(
    session: AsyncSession, tenant_id: UUID
) -> list[CompanyCapability]:
    companies = list(
        (
            await session.execute(
                CompanyRepository(session, tenant_id)
                .scoped(select(Company))
                .order_by(Company.created_at.asc())
            )
        ).scalars()
    )
    if not companies:
        return []

    async def rows(model: Any) -> list[Any]:
        repo = AssetRepository(session, tenant_id, model)
        return list((await session.execute(repo.scoped(select(model)))).scalars())

    qualifications = await rows(Qualification)
    personnel = await rows(Personnel)
    performances = await rows(Performance)
    credits = await rows(Credit)
    by_company: dict[UUID, dict[str, list[dict[str, Any]]]] = {
        company.id: {"qualifications": [], "personnel": [], "performances": [], "credits": []}
        for company in companies
    }
    for asset in qualifications:
        bucket = by_company.get(asset.company_id)
        if bucket is not None:
            bucket["qualifications"].append(
                {
                    "id": asset.id,
                    "name": asset.name,
                    "level": asset.level,
                    "cert_no": asset.cert_no,
                    "valid_to": asset.valid_to,
                }
            )
    for asset in personnel:
        bucket = by_company.get(asset.company_id)
        if bucket is not None:
            bucket["personnel"].append(
                {
                    "id": asset.id,
                    "name": asset.name,
                    "cert_type": asset.cert_type,
                    "level": asset.level,
                    "valid_to": asset.valid_to,
                }
            )
    for asset in performances:
        bucket = by_company.get(asset.company_id)
        if bucket is not None:
            bucket["performances"].append(
                {
                    "id": asset.id,
                    "project_name": asset.project_name,
                    "amount": asset.amount,
                    "category": asset.category,
                }
            )
    for asset in credits:
        bucket = by_company.get(asset.company_id)
        if bucket is not None:
            bucket["credits"].append(
                {"id": asset.id, "check_item": asset.check_item, "status": asset.status}
            )
    return [
        CompanyCapability(
            company_id=company.id,
            company_name=company.name,
            region=company.region,
            industry=company.enterprise_industry or company.industry,
            qualifications=tuple(by_company[company.id]["qualifications"]),
            personnel=tuple(by_company[company.id]["personnel"]),
            performances=tuple(by_company[company.id]["performances"]),
            credits=tuple(by_company[company.id]["credits"]),
        )
        for company in companies
    ]


async def load_user_preferences(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID | None
) -> list[MatchPreference]:
    repository = SubscriptionRepository(session, tenant_id)
    statement = repository.scoped(select(OpportunitySubscription)).where(
        OpportunitySubscription.enabled.is_(True)
    )
    if user_id is not None:
        statement = statement.where(OpportunitySubscription.user_id == user_id)
    subscriptions = list((await session.execute(statement)).scalars())
    return preferences_from_subscriptions(subscriptions)


async def _notify_recommendation(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    opportunity: Opportunity,
    match: OpportunityMatch,
) -> bool:
    existing = (
        await session.execute(
            select(Notification.id).where(
                Notification.tenant_id == tenant_id,
                Notification.user_id == user_id,
                Notification.type == "opportunity_match_recommend",
                Notification.resource_type == "opportunity",
                Notification.resource_id == str(opportunity.id),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    qualified_text = "资格自检通过" if match.qualified is True else "资格状态需复核"
    await NotificationService(session).notify(
        tenant_id=tenant_id,
        user_id=user_id,
        notification_type="opportunity_match_recommend",
        title=f"发现高匹配投标机会：{(opportunity.title or '')[:60]}",
        body=f"启发式匹配分 {match.match_score}，{qualified_text}。请到机会中心查看匹配详情。",
        resource_type="opportunity",
        resource_id=str(opportunity.id),
    )
    return True


async def ensure_project_for_match(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    opportunity: Opportunity,
    match: OpportunityMatch,
    settings: dict[str, Any],
) -> tuple[UUID | None, bool]:
    """在 advisory_lock 内检查并创建项目，保证同一机会只建一次。"""
    lock_key = f"opportunity-auto-project:{tenant_id}:{opportunity.id}"
    async with advisory_lock(lock_key) as acquired:
        existing = (
            await session.execute(
                ProjectRepository(session, tenant_id)
                .scoped(select(BidProject))
                .where(BidProject.opportunity_id == opportunity.id)
                .order_by(BidProject.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        existing_id = existing.id if existing is not None else match.project_id
        plan = project_creation_plan(
            decision=match.decision,
            auto_create_project=bool(settings["auto_create_project"]),
            existing_project_id=existing_id,
            lock_acquired=acquired,
        )
        if plan == "existing":
            if match.project_id != existing_id:
                match.project_id = existing_id
                await session.commit()
            return existing_id, False
        if plan != "create":
            return existing_id, False
        from app.modules.quota.policy import (
            enforce_project_limit,
            project_creation_guard,
        )

        async with project_creation_guard(tenant_id):
            await enforce_project_limit(session, tenant_id)
            project = BidProject(
                tenant_id=tenant_id,
                opportunity_id=opportunity.id,
                name=(opportunity.title or opportunity.project_name or "未命名投标项目")[:400],
                code=opportunity.project_code,
                buyer=opportunity.buyer,
                status="active",
                owner_id=user_id,
                bid_deadline=opportunity.bid_deadline,
            )
            session.add(project)
            await session.commit()
        await session.refresh(project)
        match.project_id = project.id
        await session.commit()
        await AuditService(session).append(
            tenant_id=tenant_id,
            actor_id=user_id,
            action="opportunity.auto_project_created",
            resource_type="bid_project",
            resource_id=str(project.id),
            data={
                "opportunity_id": str(opportunity.id),
                "match_score": match.match_score,
                "engine_version": match.engine_version,
                "heuristic": True,
            },
        )
        await session.commit()
        return project.id, True


def match_response_payload(row: OpportunityMatch) -> dict[str, Any]:
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "match_score": row.match_score,
        "hit_groups": list(row.hit_groups_json or []),
        "reasons": list(row.reasons_json or []),
        "risks": list(row.risks_json or []),
        "qualified": row.qualified,
        "qualification_gaps": list(row.qualification_gaps_json or []),
        "decision": row.decision,
        "engine_version": row.engine_version,
        "computed_at": row.computed_at,
        "project_id": row.project_id,
    }


async def compute_and_store_match(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    opportunity: Opportunity,
    preferences: list[MatchPreference],
    settings: dict[str, Any],
    capabilities: list[CompanyCapability],
    actor_id: UUID | None,
    structured_requirements: list[dict[str, Any]] | None = None,
    auto_project: bool = True,
    notify: bool = True,
) -> tuple[OpportunityMatch, bool, bool]:
    requirements = normalize_requirements(structured_requirements) or extract_qualification_requirements(
        opportunity.content_text
    )
    deadline = (
        opportunity.bid_deadline.date()
        if opportunity.bid_deadline is not None
        else None
    )
    qualification = select_best_company_qualification(requirements, capabilities, deadline)
    evaluation = evaluate_opportunity_match(opportunity, preferences, qualification, capabilities)
    if deadline is None:
        evaluation["risks"] = list(
            dict.fromkeys(
                list(evaluation.get("risks") or []) + ["招标截止日未知，资格有效期未与截止日比对"]
            )
        )[:8]
    decision = decide_match(qualification.get("qualified"), evaluation["match_score"], settings)
    repo = OpportunityMatchRepository(session, tenant_id)
    previous = (
        await session.execute(
            repo.scoped(select(OpportunityMatch)).where(
                OpportunityMatch.opportunity_id == opportunity.id
            )
        )
    ).scalar_one_or_none()
    previous_decision = previous.decision if previous is not None else None
    computed_at = datetime.now(UTC)
    values = {
        "tenant_id": tenant_id,
        "opportunity_id": opportunity.id,
        "match_score": evaluation["match_score"],
        "hit_groups_json": evaluation["hit_groups"],
        "reasons_json": evaluation["reasons"],
        "risks_json": evaluation["risks"],
        "qualified": qualification.get("qualified"),
        "qualification_gaps_json": list(qualification.get("gaps") or []),
        "decision": decision,
        "engine_version": ENGINE_VERSION,
        "computed_at": computed_at,
    }
    statement = (
        insert(OpportunityMatch)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "opportunity_id"],
            set_={key: value for key, value in values.items() if key not in {"tenant_id", "opportunity_id"}},
        )
        .returning(OpportunityMatch)
    )
    row = (await session.execute(statement)).scalar_one()
    await session.commit()
    project_created = False
    if auto_project and row.decision == "recommend":
        try:
            _, project_created = await ensure_project_for_match(
                session,
                tenant_id=tenant_id,
                user_id=actor_id,
                opportunity=opportunity,
                match=row,
                settings=settings,
            )
        except Exception:
            await session.rollback()
            logger.exception("auto project creation failed for opportunity %s", opportunity.id)
            row = (
                await session.execute(
                    repo.scoped(select(OpportunityMatch)).where(
                        OpportunityMatch.opportunity_id == opportunity.id
                    )
                )
            ).scalar_one()
    notified = False
    if (
        notify
        and actor_id is not None
        and settings["notify"]
        and row.decision == "recommend"
        and previous_decision != "recommend"
    ):
        try:
            notified = await _notify_recommendation(
                session,
                tenant_id=tenant_id,
                user_id=actor_id,
                opportunity=opportunity,
                match=row,
            )
        except Exception:
            await session.rollback()
            logger.exception("match notification failed for opportunity %s", opportunity.id)
    return row, project_created, notified


async def recompute_opportunity_match(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    opportunity_id: UUID,
    user_id: UUID | None,
    actor_id: UUID | None,
) -> tuple[OpportunityMatch, bool, bool]:
    opportunity = await OpportunityRepository(session, tenant_id).get_or_404(opportunity_id)
    settings = await get_auto_match_settings(session, tenant_id)
    preferences = await load_user_preferences(session, tenant_id=tenant_id, user_id=user_id)
    capabilities = await load_company_capabilities(session, tenant_id)
    structured_requirements = await load_constraint_requirements(
        session, tenant_id=tenant_id, opportunity_id=opportunity_id
    )
    return await compute_and_store_match(
        session,
        tenant_id=tenant_id,
        opportunity=opportunity,
        preferences=preferences,
        settings=settings,
        capabilities=capabilities,
        actor_id=actor_id,
        structured_requirements=structured_requirements,
    )


async def run_auto_match_for_tenant(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    actor_id: UUID | None,
    limit: int = 100,
    force: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": True,
        "scanned": 0,
        "matched": 0,
        "recommend": 0,
        "review": 0,
        "skip": 0,
        "projects_created": 0,
        "notifications_sent": 0,
        "errors": 0,
    }
    settings = await get_auto_match_settings(session, tenant_id)
    result["enabled"] = bool(settings["enabled"])
    if not settings["enabled"]:
        return result
    preferences = await load_user_preferences(session, tenant_id=tenant_id, user_id=user_id)
    capabilities = await load_company_capabilities(session, tenant_id)
    statement = (
        OpportunityRepository(session, tenant_id)
        .scoped(select(Opportunity))
        .outerjoin(
            OpportunityMatch,
            and_(
                OpportunityMatch.tenant_id == Opportunity.tenant_id,
                OpportunityMatch.opportunity_id == Opportunity.id,
            ),
        )
    )
    if not force:
        statement = statement.where(
            or_(
                OpportunityMatch.id.is_(None),
                OpportunityMatch.computed_at < Opportunity.fetched_at,
            )
        )
    rows = list(
        (
            await session.execute(
                statement.order_by(Opportunity.created_at.desc()).limit(limit)
            )
        ).scalars()
    )
    result["scanned"] = len(rows)
    for opportunity in rows:
        try:
            structured_requirements = await load_constraint_requirements(
                session, tenant_id=tenant_id, opportunity_id=opportunity.id
            )
            row, project_created, notified = await compute_and_store_match(
                session,
                tenant_id=tenant_id,
                opportunity=opportunity,
                preferences=preferences,
                settings=settings,
                capabilities=capabilities,
                actor_id=actor_id,
                structured_requirements=structured_requirements,
            )
        except Exception:
            await session.rollback()
            logger.exception("auto match failed for opportunity %s", opportunity.id)
            result["errors"] += 1
            continue
        result["matched"] += 1
        result[row.decision] = int(result.get(row.decision, 0)) + 1
        if project_created:
            result["projects_created"] += 1
        if notified:
            result["notifications_sent"] += 1
    return result
