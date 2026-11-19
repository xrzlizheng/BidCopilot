import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import TenantRepository
from app.modules.generation.models import BidSection
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.models import BidCommitment, BidDecisionRun

DURATION_RE = re.compile(
    r"(?:工期|服务期|实施周期|交付周期|驻场周期)\s*(?:为|约|:|：)?\s*(\d+)\s*(天|日|个月|月|周)"
)
WARRANTY_RE = re.compile(r"质保(?:期)?\s*(?:为|约|:|：)?\s*(\d+)\s*(天|日|个月|月|年)")
QUOTE_RE = re.compile(
    r"(?:投标总价|投标报价|总报价|报价总额|投标价格)[^0-9]{0,16}"
    r"(\d[\d,，]*(?:\.\d+)?)\s*(万元|元)"
)
COMMITMENT_LABELS = {
    "price": "投标报价",
    "schedule": "承诺工期",
    "warranty": "承诺质保期",
    "personnel": "拟派人员",
}


class CommitmentRepository(TenantRepository[BidCommitment]):
    model = BidCommitment


def _duration_days(value: int, unit: str) -> int:
    if unit in {"个月", "月"}:
        return value * 30
    if unit == "年":
        return value * 365
    if unit == "周":
        return value * 7
    return value


async def _project_sections_text(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> str:
    rows = (
        await session.execute(
            select(BidSection.content_text).where(
                BidSection.tenant_id == tenant_id,
                BidSection.project_id == project_id,
            )
        )
    ).scalars().all()
    return "\n".join(str(row) for row in rows if row)


async def snapshot_commitments(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser,
    quote_amount: Decimal | None = None,
    duration_days: int | None = None,
    warranty_days: int | None = None,
    personnel: list[str] | None = None,
    source: str = "manual",
) -> list[BidCommitment]:
    from app.modules.projects.service import get_project

    project = await get_project(session, tenant_id, project_id)
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
    text = await _project_sections_text(session, tenant_id, project_id)
    values: dict[tuple[str, str], dict[str, Any]] = {}

    if quote_amount is None and run is not None:
        quote_amount = _decimal((run.inputs_json or {}).get("quote_amount"))
    if quote_amount is None:
        match = QUOTE_RE.search(text)
        if match:
            quote_amount = Decimal(match.group(1).replace(",", "").replace("，", ""))
            if match.group(2) == "万元":
                quote_amount *= 10000
    if quote_amount is not None:
        values[("price", COMMITMENT_LABELS["price"])] = {
            "value_text": f"{quote_amount} 元",
            "value_amount": quote_amount,
            "value_days": None,
            "source": source if run is None else "decision-card",
        }
    if duration_days is None:
        match = DURATION_RE.search(text)
        if match:
            duration_days = _duration_days(int(match.group(1)), match.group(2))
    if duration_days is not None:
        values[("schedule", COMMITMENT_LABELS["schedule"])] = {
            "value_text": f"{duration_days} 天",
            "value_amount": None,
            "value_days": duration_days,
            "source": source,
        }
    if warranty_days is None:
        match = WARRANTY_RE.search(text)
        if match:
            warranty_days = _duration_days(int(match.group(1)), match.group(2))
    if warranty_days is not None:
        values[("warranty", COMMITMENT_LABELS["warranty"])] = {
            "value_text": f"{warranty_days} 天",
            "value_amount": None,
            "value_days": warranty_days,
            "source": source,
        }
    for name in personnel or []:
        clean = str(name).strip()
        if not clean:
            continue
        values[("personnel", f"{COMMITMENT_LABELS['personnel']}：{clean}")] = {
            "value_text": clean,
            "value_amount": None,
            "value_days": None,
            "source": source,
        }
    if not values:
        return await list_commitments(session, tenant_id, project_id)
    for (kind, label), payload in values.items():
        statement = insert(BidCommitment).values(
            tenant_id=tenant_id,
            project_id=project.id,
            kind=kind,
            label=label,
            value_text=payload["value_text"],
            value_amount=payload["value_amount"],
            value_days=payload["value_days"],
            source=payload["source"],
            confirmed_by=user.id,
            confirmed_at=None,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["project_id", "kind", "label"],
            set_={
                "value_text": statement.excluded.value_text,
                "value_amount": statement.excluded.value_amount,
                "value_days": statement.excluded.value_days,
                "source": statement.excluded.source,
                "confirmed_by": statement.excluded.confirmed_by,
            },
        )
        await session.execute(statement)
    await session.commit()
    return await list_commitments(session, tenant_id, project_id)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


async def list_commitments(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> list[BidCommitment]:
    from app.modules.projects.service import get_project

    await get_project(session, tenant_id, project_id)
    return list(
        (
            await session.execute(
                CommitmentRepository(session, tenant_id)
                .scoped(select(BidCommitment))
                .where(BidCommitment.project_id == project_id)
                .order_by(BidCommitment.kind, BidCommitment.created_at)
            )
        ).scalars()
    )


def _contract_amount(clauses: list[dict[str, Any]]) -> tuple[Decimal | None, dict[str, Any] | None]:
    from app.modules.contracts.service import parse_amount_text

    best: Decimal | None = None
    clause: dict[str, Any] | None = None
    for row in clauses:
        if row.get("category") != "price":
            continue
        amount = parse_amount_text(row.get("text") or "")
        if amount is not None and (best is None or amount > best):
            best = amount
            clause = row
    return best, clause


def _contract_days(clauses: list[dict[str, Any]], pattern: re.Pattern[str]) -> int | None:
    for row in clauses:
        match = pattern.search(row.get("text") or "")
        if match:
            return _duration_days(int(match.group(1)), match.group(2))
    return None


def compare_contract(
    clauses: list[dict[str, Any]], commitments: list[BidCommitment]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    full_text = "\n".join(row.get("text") or "" for row in clauses)
    for commitment in commitments:
        if commitment.kind == "price" and commitment.value_amount is not None:
            if commitment.value_amount <= 0:
                continue
            amount, clause = _contract_amount(clauses)
            if amount is None or amount <= 0:
                continue
            delta = abs(amount - commitment.value_amount) / commitment.value_amount
            if delta > Decimal("0.01"):
                findings.append(
                    {
                        "kind": "deviation",
                        "rule_id": "commitment_price_mismatch",
                        "severity": "high",
                        "message": (
                            f"合同总价 {amount} 元与投标承诺 {commitment.value_amount} 元偏离 {delta:.1%}"
                        ),
                        "legal_basis": "《招标投标法》第四十六条：不得再行订立背离合同实质性内容的其他协议",
                        "evidence_span": {"commitment": commitment.value_text},
                        "clause_id_seq": clause.get("seq") if clause else None,
                        "source": "rule",
                    }
                )
        elif commitment.kind == "schedule" and commitment.value_days is not None:
            days = _contract_days(clauses, DURATION_RE)
            if days is not None and abs(days - commitment.value_days) > 7:
                findings.append(
                    {
                        "kind": "deviation",
                        "rule_id": "commitment_schedule_mismatch",
                        "severity": "medium",
                        "message": f"合同工期 {days} 天与投标承诺 {commitment.value_days} 天不一致",
                        "legal_basis": "招标文件/投标承诺的工期属于合同实质性内容",
                        "evidence_span": {"commitment": commitment.value_text},
                        "clause_id_seq": None,
                        "source": "rule",
                    }
                )
        elif commitment.kind == "warranty" and commitment.value_days is not None:
            days = _contract_days(clauses, WARRANTY_RE)
            if days is not None and abs(days - commitment.value_days) > 7:
                findings.append(
                    {
                        "kind": "deviation",
                        "rule_id": "commitment_warranty_mismatch",
                        "severity": "medium",
                        "message": f"合同质保期 {days} 天与投标承诺 {commitment.value_days} 天不一致",
                        "legal_basis": "质保期属于合同实质性内容",
                        "evidence_span": {"commitment": commitment.value_text},
                        "clause_id_seq": None,
                        "source": "rule",
                    }
                )
        elif commitment.kind == "personnel":
            name = commitment.value_text
            if name and name not in full_text:
                findings.append(
                    {
                        "kind": "missing",
                        "rule_id": "commitment_personnel_absent",
                        "severity": "medium",
                        "message": f"合同未列明投标承诺人员：{name}",
                        "legal_basis": "拟派人员属于投标承诺，合同与投标文件应保持一致",
                        "evidence_span": {"commitment": name},
                        "clause_id_seq": None,
                        "source": "rule",
                    }
                )
    return findings
