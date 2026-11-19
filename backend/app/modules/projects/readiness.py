import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.repository import TenantRepository
from app.modules.compliance.models import ComplianceReport, Finding
from app.modules.projects.models import (
    ComplianceResponse,
    ProjectReadinessItem,
)
from app.modules.projects.service import get_latest_constraint, get_project

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "data" / "readiness"
DEFAULT_TEMPLATE = "it_integration"
READINESS_STATUSES = ("pending", "done", "blocked", "not_applicable", "waived")
RESPONSE_RULES = {"RESP-OPEN-001", "RESP-LINK-001"}


class ReadinessRepository(TenantRepository[ProjectReadinessItem]):
    model = ProjectReadinessItem


def list_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        template = json.loads(path.read_text(encoding="utf-8"))
        templates.append(
            {
                "code": template["code"],
                "name": template["name"],
                "version": template["version"],
                "description": template.get("description", ""),
                "item_count": len(template.get("items") or []),
            }
        )
    return templates


def get_template(code: str) -> dict[str, Any]:
    # code 来自查询参数：只允许已知字形的模板文件名，阻断路径穿越与任意 .json 探测。
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", code or ""):
        raise NotFoundError("检查清单模板不存在")
    path = TEMPLATE_DIR / f"{code}.json"
    if not path.exists():
        raise NotFoundError("检查清单模板不存在")
    template = json.loads(path.read_text(encoding="utf-8"))
    if template.get("code") != code or not template.get("items"):
        raise ValidationAppError("检查清单模板格式无效")
    return template


def _due_at(project: Any, offset_days: Any) -> datetime | None:
    base = project.internal_deadline or project.bid_deadline
    if base is None:
        return None
    try:
        days = int(offset_days or 0)
    except (TypeError, ValueError):
        days = 0
    return base + timedelta(days=days)


async def ensure_readiness(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    template_code: str | None = None,
) -> tuple[list[ProjectReadinessItem], bool]:
    project = await get_project(session, tenant_id, project_id)
    # GET 也会触发首次初始化：事务级锁串行化并发首访，避免唯一约束撞车 500。
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"project_readiness:{project_id}"},
    )
    code = template_code or project.readiness_template_code or DEFAULT_TEMPLATE
    template = get_template(code)
    version = int(template["version"])
    repo = ReadinessRepository(session, tenant_id)
    existing = list(
        (
            await session.execute(
                repo.scoped(
                    select(ProjectReadinessItem)
                    .where(ProjectReadinessItem.project_id == project_id)
                    .order_by(ProjectReadinessItem.sort_order)
                )
            )
        ).scalars()
    )
    if existing:
        if project.readiness_template_code and project.readiness_template_code != code:
            raise ConflictError("项目已使用其他检查清单模板，不能静默切换")
        return existing, False

    project.readiness_template_code = code
    project.readiness_template_version = version
    created: list[ProjectReadinessItem] = []
    for row in template["items"]:
        source_type = str(row.get("source_type") or "manual")
        item = ProjectReadinessItem(
            tenant_id=tenant_id,
            project_id=project_id,
            template_code=code,
            template_version=version,
            item_key=str(row["item_key"]),
            category=str(row.get("category") or "其他"),
            label=str(row["label"]),
            source_type=source_type,
            rule_id=row.get("rule_id"),
            is_blocking=bool(row.get("is_blocking")),
            applicability=str(row.get("applicability") or "required"),
            evidence_required=bool(row.get("evidence_required")),
            status="pending",
            owner_id=project.owner_id,
            due_at=_due_at(project, row.get("due_offset_days")),
            source_state="stale" if source_type == "auto" else "fresh",
            stage=str(row.get("stage") or ""),
            sort_order=int(row.get("sort_order") or 0),
        )
        session.add(item)
        created.append(item)
    await session.commit()
    for item in created:
        await session.refresh(item)
    return created, True


async def _latest_report(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> Any:
    return (
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


async def _response_state(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> tuple[int, int, int]:
    """返回 (total, unresolved, responded_without_section)。"""
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        return 0, 0, 0
    rows = list(
        (
            await session.execute(
                select(ComplianceResponse).where(
                    ComplianceResponse.tenant_id == tenant_id,
                    ComplianceResponse.project_id == project_id,
                    ComplianceResponse.constraint_id == constraint.id,
                )
            )
        ).scalars()
    )
    unresolved = sum(
        1
        for row in rows
        if row.response_status in {"pending", "deviated", "not_responded"}
    )
    unlinked = sum(
        1
        for row in rows
        if row.response_status == "responded" and not (row.section_ref or "").strip()
    )
    return len(rows), unresolved, unlinked


async def refresh_auto_items(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
) -> list[ProjectReadinessItem]:
    await get_project(session, tenant_id, project_id)
    repo = ReadinessRepository(session, tenant_id)
    items = list(
        (
            await session.execute(
                repo.scoped(
                    select(ProjectReadinessItem)
                    .where(ProjectReadinessItem.project_id == project_id)
                    .order_by(ProjectReadinessItem.sort_order)
                )
            )
        ).scalars()
    )
    if not items:
        return items
    report = await _latest_report(session, tenant_id, project_id)
    findings: dict[str, Finding] = {}
    if report is not None:
        rows = list(
            (
                await session.execute(
                    select(Finding).where(
                        Finding.tenant_id == tenant_id,
                        Finding.report_id == report.id,
                    )
                )
            ).scalars()
        )
        findings = {row.rule_id: row for row in rows}
    total, unresolved, unlinked = await _response_state(session, tenant_id, project_id)
    now = datetime.now(UTC)

    for item in items:
        if item.source_type != "auto":
            continue
        source_fresh = report is not None or item.rule_id in RESPONSE_RULES
        if item.rule_id in RESPONSE_RULES:
            if total == 0:
                item.status = "pending"
                item.source_state = "stale"
            elif item.rule_id == "RESP-OPEN-001":
                item.status = "blocked" if unresolved else "done"
                item.source_state = "fresh"
            else:
                item.status = "blocked" if unlinked else "done"
                item.source_state = "fresh"
            item.finding_id = None
        elif not source_fresh:
            item.status = "pending"
            item.source_state = "stale"
            item.finding_id = None
        else:
            finding = findings.get(str(item.rule_id))
            item.status = "blocked" if finding is not None else "done"
            item.source_state = "fresh"
            item.finding_id = finding.id if finding is not None else None
        item.synced_at = now
    await session.commit()
    return items


def _serialize(item: ProjectReadinessItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "item_key": item.item_key,
        "template_code": item.template_code,
        "template_version": item.template_version,
        "category": item.category,
        "label": item.label,
        "source_type": item.source_type,
        "rule_id": item.rule_id,
        "is_blocking": item.is_blocking,
        "applicability": item.applicability,
        "evidence_required": item.evidence_required,
        "status": item.status,
        "owner_id": str(item.owner_id) if item.owner_id else None,
        "collaborator_ids": [str(value) for value in item.collaborator_ids or []],
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "evidence_refs": item.evidence_refs or [],
        "note": item.note,
        "waived_reason": item.waived_reason,
        "confirmed_by": str(item.confirmed_by) if item.confirmed_by else None,
        "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
        "source_state": item.source_state,
        "synced_at": item.synced_at.isoformat() if item.synced_at else None,
        "stage": item.stage,
        "sort_order": item.sort_order,
    }


def readiness_payload(project: Any, template: dict[str, Any], items: list[ProjectReadinessItem]) -> dict[str, Any]:
    counts = {status: 0 for status in READINESS_STATUSES}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    blocking_open = sum(
        1
        for item in items
        if item.is_blocking and item.status in {"pending", "blocked"}
    )
    settled = counts["done"] + counts["not_applicable"] + counts["waived"]
    return {
        "template": {
            "code": template["code"],
            "name": template["name"],
            "version": template["version"],
            "description": template.get("description", ""),
        },
        "bid_deadline": project.bid_deadline.isoformat() if project.bid_deadline else None,
        "internal_deadline": project.internal_deadline.isoformat()
        if project.internal_deadline
        else None,
        "items": [_serialize(item) for item in items],
        "summary": {
            "total": len(items),
            "done": counts["done"],
            "blocked": counts["blocked"],
            "pending": counts["pending"],
            "not_applicable": counts["not_applicable"],
            "waived": counts["waived"],
            "blocking_open": blocking_open,
            "completion_rate": round(settled / len(items), 3) if items else 0.0,
        },
    }


async def update_readiness_item(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    item_id: UUID,
    *,
    user_id: UUID,
    status: str | None = None,
    note: str | None = None,
    owner_id: UUID | None = None,
    collaborator_ids: list[UUID] | None = None,
    due_at: datetime | None = None,
    evidence_refs: list[str] | None = None,
    waived_reason: str | None = None,
) -> ProjectReadinessItem:
    await get_project(session, tenant_id, project_id)
    repo = ReadinessRepository(session, tenant_id)
    item = (
        await session.execute(
            repo.scoped(
                select(ProjectReadinessItem).where(
                    ProjectReadinessItem.id == item_id,
                    ProjectReadinessItem.project_id == project_id,
                )
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("检查项不存在")
    if item.source_type == "auto" and (
        status is not None or evidence_refs is not None or waived_reason is not None
    ):
        raise ValidationAppError("自动项由检查结果驱动，不能手工改状态/证据/豁免")

    if status is not None and status not in READINESS_STATUSES:
        raise ValidationAppError("无效的检查项状态")
    if note is not None:
        item.note = note
    if owner_id is not None:
        item.owner_id = owner_id
    if collaborator_ids is not None:
        item.collaborator_ids = [str(value) for value in collaborator_ids]
    if due_at is not None:
        item.due_at = due_at
    if evidence_refs is not None:
        item.evidence_refs = evidence_refs
    if waived_reason is not None:
        item.waived_reason = waived_reason

    if status is not None:
        if status in {"not_applicable", "waived"} and not (
            waived_reason or item.waived_reason or note or item.note
        ):
            raise ValidationAppError("标记不适用/豁免必须填写理由")
        if status == "done":
            if (item.is_blocking or item.evidence_required) and not item.evidence_refs:
                raise ValidationAppError("阻断项完成前必须提供证据")
            item.completed_at = datetime.now(UTC)
            item.confirmed_by = user_id
            item.confirmed_at = datetime.now(UTC)
            item.waived_by = None
            item.waived_at = None
            item.waived_reason = None
        elif status == "pending":
            item.completed_at = None
            item.confirmed_by = None
            item.confirmed_at = None
            item.waived_by = None
            item.waived_at = None
            item.waived_reason = None
        elif status == "blocked":
            item.completed_at = None
            item.confirmed_by = None
            item.confirmed_at = None
        else:
            item.completed_at = None
            item.confirmed_by = None
            item.confirmed_at = None
            item.waived_by = user_id
            item.waived_at = datetime.now(UTC)
        item.status = status
    item.source_state = "fresh"
    await session.commit()
    await session.refresh(item)
    return item
