from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.db.locks import advisory_lock
from app.modules.assets.enterprise_scale import classify_enterprise, is_sme
from app.modules.assets.models import Company
from app.modules.compliance.models import ComplianceReport, Finding
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.decision_evidence import (
    collect_decision_evidence,
    competition_signal,
)
from app.modules.projects.matching import legacy_decision_payload
from app.modules.projects.models import BidDecisionRun, BidProject
from app.modules.projects.service import (
    build_matrix,
    get_latest_constraint,
    get_matrix,
    get_project,
    qualification_check,
)
from app.modules.system.models import TenantSetting

ENGINE_VERSION = "decision-v2"
# 六维权重（和为 100）。竞争情报 10 分来自资格 30→25、经济性 25→20 的让渡：
# 资格仍是第一权重（硬性不满足直接废标），但历史中标价/竞争格局过去完全没进
# 模型，导致「五维都在 70 分以上、却明知陪跑」的单一结论。10 分不足以推翻
# 资格/风险否决，只调整「投不投」的边际倾向。
DEFAULT_WEIGHTS = {
    "qualification": 25,
    "economics": 20,
    "cost": 20,
    "risk": 15,
    "resource": 10,
    "competition": 10,
}
DEFAULT_THRESHOLDS = {"invest_min": 70, "pending_min": 50}
DIMENSION_LABELS = {
    "qualification": "资格匹配度",
    "economics": "经济性（比率口径）",
    "cost": "成本底线（本单绝对口径）",
    "risk": "风险项",
    "resource": "资源占用",
    "competition": "竞争情报（历史中标/胜率）",
}
STATUS_VALUES = {"Go", "Conditional Go", "No-Go"}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _clamp_score(value: Decimal) -> float:
    return float(max(Decimal("0"), min(Decimal("100"), value)))


async def _setting(
    session: AsyncSession, tenant_id: UUID, key: str, default: dict[str, Any]
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == key,
            )
        )
    ).scalar_one_or_none()
    value = dict(default)
    if row is not None and isinstance(row.value, dict):
        value.update(row.value)
    return value


async def _latest_report(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> tuple[ComplianceReport | None, list[Finding]]:
    report = (
        await session.execute(
            select(ComplianceReport)
            .where(
                ComplianceReport.tenant_id == tenant_id,
                ComplianceReport.project_id == project_id,
            )
            .order_by(ComplianceReport.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if report is None:
        return None, []
    findings = list(
        (
            await session.execute(
                select(Finding).where(
                    Finding.tenant_id == tenant_id, Finding.report_id == report.id
                )
            )
        ).scalars()
    )
    return report, findings


def _external_status(computed_status: str) -> str:
    return "Conditional Go" if computed_status == "insufficient_data" else computed_status


def _dimension(
    key: str,
    score: float | None,
    weight: float,
    reason: str,
    *,
    scored: bool,
    source: str = "tool-default",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": DIMENSION_LABELS[key],
        "score": score,
        "weight": weight,
        "scored": scored,
        "reason": reason,
        "source": source,
    }


async def _qualification_dimension(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser,
    weight: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items, _ = await qualification_check(session, tenant_id, project_id, user=user)
    statuses = [item.status for item in items]
    vetoes: list[dict[str, Any]] = []
    if "FAIL" in statuses:
        vetoes.append(
            {
                "code": "QUALIFICATION_FAIL",
                "label": "资格硬性不满足",
                "reason": "资格预审存在 FAIL 项",
            }
        )
        return _dimension("qualification", 0, weight, "资格预审存在 FAIL", scored=True), vetoes
    if "UNKNOWN" in statuses:
        return _dimension(
            "qualification", 60, weight, "资格预审存在待人工确认项", scored=True
        ), vetoes
    if not items:
        return _dimension("qualification", 60, weight, "没有可判定的资格要求", scored=True), vetoes
    return _dimension("qualification", 100, weight, "资格预审全部 PASS", scored=True), vetoes


async def _resource_dimension(
    session: AsyncSession,
    project: BidProject,
    weight: float,
) -> dict[str, Any]:
    if project.owner_id is None or project.bid_deadline is None:
        return _dimension(
            "resource", None, weight, "缺少负责人或投标截止，无法评估资源冲突", scored=False
        )
    window = timedelta(days=7)
    conflicts = (
        await session.execute(
            select(func.count())
            .select_from(BidProject)
            .where(
                BidProject.tenant_id == project.tenant_id,
                BidProject.id != project.id,
                BidProject.status == "active",
                BidProject.owner_id == project.owner_id,
                BidProject.bid_deadline.is_not(None),
                BidProject.bid_deadline >= project.bid_deadline - window,
                BidProject.bid_deadline <= project.bid_deadline + window,
            )
        )
    ).scalar_one()
    score = max(0, 100 - 25 * int(conflicts or 0))
    reason = f"同负责人 7 天内另有 {int(conflicts or 0)} 个在建项目" if conflicts else "同负责人近期无冲突项目"
    return _dimension("resource", float(score), weight, reason, scored=True)


async def _competition_dimension(
    session: AsyncSession,
    tenant_id: UUID,
    project: BidProject,
    weight: float,
) -> dict[str, Any]:
    """竞争情报：把采购人历史中标/竞争供应商/我方胜率/预算锚点折成一个维度。

    全部来自本租户历史表（award_records / opportunities / bid_projects），
    样本不足时 scored=False 且不参与加权——绝不拿默认分补位。
    """
    evidence = await collect_decision_evidence(
        session, tenant_id=tenant_id, project_id=project.id, project=project
    )
    items = {item["key"]: item for item in evidence["items"]}
    signal = competition_signal(items)
    return _dimension(
        "competition",
        signal["score"],
        weight,
        signal["reason"],
        scored=signal["scored"],
        source=signal["source"],
    )


async def _risk_dimension(
    session: AsyncSession,
    project: BidProject,
    payload: dict[str, Any],
    weight: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report, findings = await _latest_report(session, project.tenant_id, project.id)
    if project.bid_deadline is None and report is None:
        return _dimension("risk", None, weight, "缺少截止时间与合规报告", scored=False), []
    vetoes: list[dict[str, Any]] = []
    score = Decimal("100")
    reasons: list[str] = []
    if project.bid_deadline is not None and project.bid_deadline < datetime.now(UTC):
        vetoes.append(
            {"code": "DEADLINE_PASSED", "label": "投标截止已过", "reason": "项目已过投标截止时间"}
        )
        score = Decimal("0")
        reasons.append("投标截止已过")
    hard = sum(1 for item in findings if item.finding_type == "HARD")
    soft = len(findings) - hard
    if hard or soft:
        score -= Decimal(hard * 15 + soft * 5)
        reasons.append(f"合规 findings：HARD {hard} / SOFT {soft}")

    # 投标风险画像：废标项不参与打分，直接作为「必须人工确认」的前置条件。
    # 企业规模/资质是否满足无法自动判定（Company 表没有规模字段），
    # 所以这里只扣分 + 提示，绝不自动下 Go 结论——判错的代价是废标。
    bid_risks = payload.get("bid_risks") or {}
    risk_items = bid_risks.get("items") or []
    blockers = [item for item in risk_items if item.get("level") == "blocker"]
    if blockers:
        penalty = Decimal(min(60, 20 * len(blockers)))
        score -= penalty
        names = "、".join(str(item.get("name")) for item in blockers[:3])
        reasons.append(f"废标风险 {len(blockers)} 项（{names}），须逐项确认")
    flags = bid_risks.get("flags") or {}
    if flags.get("sme_only"):
        # 已确认企业规模时给硬结论；未维护规模则只能提示——「中小企业」判定有
        # 明确国标（工信部联企业〔2011〕300 号），不该靠感觉填。
        scale = await _company_scale_for(session, project)
        if scale["is_sme"] is False:
            vetoes.append(
                {
                    "code": "SME_ONLY_NOT_SME",
                    "label": "非中小企业不可投",
                    "reason": f"本项目专门面向中小企业，本企业划型为「{scale['scale']}」",
                }
            )
            score = Decimal("0")
            reasons.append(f"专门面向中小企业，本企业为{scale['scale']}企业")
        elif scale["is_sme"] is True:
            reasons.append(f"专门面向中小企业，本企业为{scale['scale']}企业（符合）")
        else:
            score -= Decimal("10")
            reasons.append("专门面向中小企业：尚未维护企业规模，无法判定是否符合")
    deposit = payload.get("deposit") or {}
    budget = payload.get("budget") or {}
    try:
        deposit_amount = _decimal(deposit.get("amount"))
        budget_amount = _decimal(budget.get("amount"))
    except (InvalidOperation, ValueError):
        deposit_amount = budget_amount = None
    if (
        deposit_amount is not None
        and budget_amount is not None
        and str(budget.get("label") or "") in {"最高限价", "控制价", "招标控制价", "估算价", "采购估算价"}
        and deposit_amount > budget_amount * Decimal("0.02")
    ):
        score -= Decimal("20")
        reasons.append("保证金超过估算价 2%")
    return _dimension(
        "risk",
        _clamp_score(score),
        weight,
        "；".join(reasons) or "未发现截止/合规/保证金风险",
        scored=True,
    ), vetoes


async def _company_scale_for(session: AsyncSession, project: BidProject) -> dict[str, Any]:
    """取项目所属企业的划型；无关联企业或指标不全时返回 is_sme=None。"""
    if project.company_id is None:
        return {"scale": None, "is_sme": None}
    company = (
        await session.execute(
            select(Company).where(
                Company.tenant_id == project.tenant_id,
                Company.id == project.company_id,
            )
        )
    ).scalar_one_or_none()
    if company is None:
        return {"scale": None, "is_sme": None}
    result = classify_enterprise(
        industry=company.enterprise_industry or company.industry,
        employees=company.employee_count,
        revenue=company.annual_revenue,
        total_assets=company.total_assets,
    )
    # 人工确认值优先
    if company.enterprise_scale_source == "manual" and company.enterprise_scale:
        return {"scale": company.enterprise_scale, "is_sme": is_sme(company.enterprise_scale)}
    return {"scale": result.scale, "is_sme": is_sme(result.scale)}


def _decision_summary(
    status: str, score: float | None, vetoes: list[dict[str, Any]], scored_count: int
) -> str:
    if vetoes:
        return "存在硬否决：" + "、".join(item["label"] for item in vetoes)
    if status == "insufficient_data":
        return f"仅 {scored_count} 个维度有数据，暂不能给出 Go"
    if status == "Go":
        return f"综合得分 {score:.1f}，建议进入投标准备"
    if status == "Conditional Go":
        return f"综合得分 {score:.1f}，需补齐证据/确认风险后再升级"
    return f"综合得分 {score:.1f}，建议不投或先整改"


async def _create_run(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: BidProject,
    constraint: Any,
    user: CurrentUser,
    inputs: dict[str, Any],
    dimensions: list[dict[str, Any]],
    vetoes: list[dict[str, Any]],
    score: float | None,
    computed_status: str,
    thresholds: dict[str, Any],
    source_labels: dict[str, Any],
    engine_mode: str = "full",
    engine_version: str = ENGINE_VERSION,
    summary: str | None = None,
    factors: list[dict[str, str]] | None = None,
) -> BidDecisionRun:
    async with advisory_lock(f"decision-card:{project.id}") as acquired:
        if not acquired:
            raise ValidationAppError("该项目正在生成决策卡，请稍后重试")
        run_no = int(
            (
                await session.execute(
                    select(func.max(BidDecisionRun.run_no)).where(
                        BidDecisionRun.tenant_id == tenant_id,
                        BidDecisionRun.project_id == project.id,
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        run = BidDecisionRun(
            tenant_id=tenant_id,
            project_id=project.id,
            run_no=run_no,
            engine_mode=engine_mode,
            engine_version=engine_version,
            constraint_id=constraint.id if constraint else None,
            constraint_version=constraint.version if constraint else None,
            company_id=project.company_id,
            profile_snapshot={
                "company_id": str(project.company_id) if project.company_id else None,
                "owner_id": str(project.owner_id) if project.owner_id else None,
                "bid_deadline": project.bid_deadline.isoformat()
                if project.bid_deadline
                else None,
                # 划型快照：企业规模会变（补材料、年末结算），半年后回头看
                # 「当初为什么判 No-Go」必须能答上来，否则结论不可审计。
                "enterprise_scale": await _company_scale_for(session, project),
            },
            inputs_json=inputs,
            dimensions_json=dimensions,
            vetoes_json=vetoes,
            score=score,
            computed_status=computed_status,
            thresholds_json=thresholds,
            source_labels=source_labels,
            created_by=user.id,
        )
        session.add(run)
        external_status = _external_status(computed_status)
        project.decision_status = external_status
        project.decision_at = datetime.now(UTC)
        project.decision_by = user.id
        project.decision_json = {
            "status": external_status,
            "summary": summary
            or _decision_summary(
                computed_status, score, vetoes, sum(1 for d in dimensions if d["scored"])
            ),
            "factors": factors
            or [
                {
                    "name": d["label"],
                    "value": f"{d['score']:.1f}" if d["score"] is not None else "未计入",
                }
                for d in dimensions
            ],
        }
        await session.commit()
        await session.refresh(run)
        return run


async def compute_legacy_decision(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser,
) -> tuple[dict[str, Any], list[Any], dict[str, int]]:
    """旧 Bid-No-Bid 接口的兼容模式：同一引擎、同一 runs 表，只用资格 + 证据覆盖两维。"""
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    qualifications, qual_summary = await qualification_check(
        session, tenant_id, project_id, user=user
    )
    matrix = await get_matrix(session, tenant_id, project_id)
    if not matrix:
        matrix = [
            row.model_dump()
            for row in await build_matrix(session, tenant_id, project_id, user=user)
        ]
    payload = legacy_decision_payload(
        [item.model_dump() for item in qualifications], matrix
    )
    statuses = [item.status for item in qualifications]
    coverage = [row.get("coverage_status") for row in matrix]
    # 显式标注：字面量里混合 str/int/float/bool，不标注会被推断成 list[dict[str, object]]，
    # 与 _create_run 的 list[dict[str, Any]] 参数因不变性不兼容。
    dimensions: list[dict[str, Any]] = [
        {
            "key": "qualification",
            "label": DIMENSION_LABELS["qualification"],
            "score": 0 if "FAIL" in statuses else (60 if "UNKNOWN" in statuses else 100),
            "weight": 50,
            "scored": True,
            "reason": f"PASS {statuses.count('PASS')} / FAIL {statuses.count('FAIL')} / UNKNOWN {statuses.count('UNKNOWN')}",
            "source": "legacy",
        },
        {
            "key": "evidence",
            "label": "评分证据覆盖",
            "score": (
                round(
                    100
                    * coverage.count("covered")
                    / len(coverage),
                    2,
                )
                if coverage
                else None
            ),
            "weight": 50,
            "scored": bool(coverage),
            "reason": (
                f"covered {coverage.count('covered')} / partial {coverage.count('partial')} / missing {coverage.count('missing')}"
            ),
            "source": "legacy",
        },
    ]
    await _create_run(
        session,
        tenant_id=tenant_id,
        project=project,
        constraint=constraint,
        user=user,
        inputs={},
        dimensions=dimensions,
        vetoes=[],
        score=None,
        computed_status=payload["status"],
        thresholds={},
        source_labels={"engine": "legacy"},
        engine_mode="legacy",
        engine_version="decision-v1-legacy",
        summary=payload["summary"],
        factors=payload["factors"],
    )
    return payload, list(qualifications), qual_summary


async def compute_decision_card(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser,
    inputs: dict[str, Any] | None = None,
) -> BidDecisionRun:
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        raise ValidationAppError("请先上传并解析招标文件")
    payload = constraint.payload or {}
    inputs = inputs or {}
    # 只认 DEFAULT_WEIGHTS 里的键：老租户存过五维权重时，新增的 competition
    # 维度必须回落到默认值，不能让缺失键把权重表打穿（KeyError）或被静默跳过。
    raw_weights = await _setting(session, tenant_id, "decision_weights", DEFAULT_WEIGHTS)
    weights = {
        key: float(raw_weights.get(key, default)) for key, default in DEFAULT_WEIGHTS.items()
    }
    thresholds = await _setting(session, tenant_id, "decision_thresholds", DEFAULT_THRESHOLDS)
    cost_model = await _setting(session, tenant_id, "decision_cost_model", {})

    dimension_rows: list[dict[str, Any]] = []
    vetoes: list[dict[str, Any]] = []

    qualification, qualification_vetoes = await _qualification_dimension(
        session, tenant_id, project_id, user, float(weights["qualification"])
    )
    dimension_rows.append(qualification)
    vetoes.extend(qualification_vetoes)

    ratio_dimension = _dimension(
        "economics",
        None,
        float(weights["economics"]),
        "租户未校准直接/间接/税费率，比率口径未计入",
        scored=False,
        source="tenant-setting",
    )
    try:
        direct = _decimal(cost_model.get("direct_rate"))
        indirect = _decimal(cost_model.get("indirect_rate"))
        tax = _decimal(cost_model.get("tax_rate"))
    except (InvalidOperation, ValueError):
        direct = indirect = tax = None
    if direct is not None and indirect is not None and tax is not None:
        ratio_margin = Decimal("1") - direct - indirect - tax
        ratio_dimension = _dimension(
            "economics",
            _clamp_score(ratio_margin / Decimal("0.12") * 100),
            float(weights["economics"]),
            f"比率口径净利率 {ratio_margin:.2%}",
            scored=True,
            source="tenant-setting",
        )
    dimension_rows.append(ratio_dimension)

    quote = _decimal(inputs.get("quote_amount"))
    estimated_cost = _decimal(inputs.get("estimated_cost"))
    tax_rate = _decimal(inputs.get("tax_rate"))
    if quote is not None and quote > 0 and estimated_cost is not None and tax_rate is not None:
        margin = (quote * (Decimal("1") - tax_rate) - estimated_cost) / quote
        cost_dimension = _dimension(
            "cost",
            _clamp_score(margin / Decimal("0.12") * 100),
            float(weights["cost"]),
            f"本单绝对净利率 {margin:.2%}",
            scored=True,
        )
        if margin < 0:
            vetoes.append(
                {
                    "code": "BELOW_COST",
                    "label": "报价低于估算成本",
                    "reason": f"本单绝对净利率 {margin:.2%}",
                }
            )
        dimension_rows.append(cost_dimension)
    else:
        dimension_rows.append(
            _dimension(
                "cost",
                None,
                float(weights["cost"]),
                "缺少报价/估算成本/税率，成本底线未计入",
                scored=False,
            )
        )

    risk, risk_vetoes = await _risk_dimension(session, project, payload, float(weights["risk"]))
    dimension_rows.append(risk)
    vetoes.extend(risk_vetoes)
    dimension_rows.append(
        await _resource_dimension(session, project, float(weights["resource"]))
    )
    competition_dimension = await _competition_dimension(
        session,
        tenant_id,
        project,
        float(weights.get("competition", DEFAULT_WEIGHTS["competition"])),
    )
    dimension_rows.append(competition_dimension)

    scored = [row for row in dimension_rows if row["scored"] and row["score"] is not None]
    weight_sum = sum(float(row["weight"]) for row in scored)
    score = (
        sum(float(row["score"]) * float(row["weight"]) for row in scored) / weight_sum
        if weight_sum
        else None
    )
    cost_scored = any(
        row["key"] == "cost" and row["scored"] and row["score"] is not None
        for row in dimension_rows
    )
    if vetoes:
        computed_status = "No-Go"
    elif len(scored) < 3 or score is None:
        computed_status = "insufficient_data"
    elif score is not None and score >= float(thresholds.get("invest_min", 70)):
        computed_status = "Conditional Go" if not cost_scored else "Go"
    elif score is not None and score >= float(thresholds.get("pending_min", 50)):
        computed_status = "Conditional Go"
    else:
        computed_status = "No-Go"

    source_labels = {
        "weights": "tool-default",
        "thresholds": "tool-default",
        "ratio_cost_model": "tenant-setting" if ratio_dimension["scored"] else "missing",
        "competition": competition_dimension["source"]
        if competition_dimension["scored"]
        else "missing",
    }
    return await _create_run(
        session,
        tenant_id=tenant_id,
        project=project,
        constraint=constraint,
        user=user,
        inputs={
            key: str(value) if value is not None else None
            for key, value in {
                "quote_amount": quote,
                "estimated_cost": estimated_cost,
                "tax_rate": tax_rate,
                "competition_intensity": inputs.get("competition_intensity"),
            }.items()
        },
        dimensions=dimension_rows,
        vetoes=vetoes,
        score=score,
        computed_status=computed_status,
        thresholds=thresholds,
        source_labels=source_labels,
    )


def serialize_decision_run(run: BidDecisionRun, *, include_inputs: bool) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "run_no": run.run_no,
        "engine_mode": run.engine_mode,
        "engine_version": run.engine_version,
        "constraint_version": run.constraint_version,
        "score": float(run.score) if run.score is not None else None,
        "computed_status": run.computed_status,
        "status": run.human_status or _external_status(run.computed_status),
        "human_status": run.human_status,
        "dimensions": run.dimensions_json or [],
        "vetoes": run.vetoes_json or [],
        "thresholds": run.thresholds_json or {},
        "source_labels": run.source_labels or {},
        "inputs": (run.inputs_json or {}) if include_inputs else None,
        "profile_snapshot": run.profile_snapshot or {},
        "created_at": run.created_at.isoformat(),
        "confirmed_at": run.confirmed_at.isoformat() if run.confirmed_at else None,
        "override_reason": run.override_reason,
    }


async def latest_decision_card(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> BidDecisionRun:
    await get_project(session, tenant_id, project_id)
    run = (
        await session.execute(
            select(BidDecisionRun)
            .where(
                BidDecisionRun.tenant_id == tenant_id,
                BidDecisionRun.project_id == project_id,
                BidDecisionRun.engine_mode == "full",
            )
            .order_by(BidDecisionRun.run_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("决策卡不存在，请先生成")
    return run


async def confirm_decision_card(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    run_id: UUID,
    user: CurrentUser,
    human_status: str,
    override_reason: str | None,
) -> BidDecisionRun:
    if human_status not in STATUS_VALUES:
        raise ValidationAppError("无效的决策状态")
    project = await get_project(session, tenant_id, project_id)
    run = (
        await session.execute(
            select(BidDecisionRun).where(
                BidDecisionRun.tenant_id == tenant_id,
                BidDecisionRun.project_id == project_id,
                BidDecisionRun.id == run_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("决策卡不存在")
    external = _external_status(run.computed_status)
    if human_status != external and not (override_reason or "").strip():
        raise ValidationAppError("人工覆盖决策状态必须填写理由")
    run.human_status = human_status
    run.confirmed_by = user.id
    run.confirmed_at = datetime.now(UTC)
    run.override_reason = override_reason
    project.decision_status = human_status
    project.decision_at = datetime.now(UTC)
    project.decision_by = user.id
    await session.commit()
    await session.refresh(run)
    return run
