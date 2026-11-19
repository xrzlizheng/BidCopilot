import json
import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import has_permission
from app.modules.audit.models import AuditEvent
from app.modules.compliance.models import ComplianceReport, Finding
from app.modules.compliance.service import redact_findings_for_user
from app.modules.generation.models import BidSection
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.decision_card import (
    latest_decision_card,
    serialize_decision_run,
)
from app.modules.projects.readiness import (
    ensure_readiness,
    get_template,
    readiness_payload,
    refresh_auto_items,
)
from app.modules.projects.responses_export import build_responses_xlsx
from app.modules.projects.service import (
    get_project,
    list_compliance_responses,
)

PACK_VERSION = "evidence-pack-v1"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _finding_dict(finding: Finding) -> dict[str, Any]:
    return {
        "id": str(finding.id),
        "rule_id": finding.rule_id,
        "category": finding.category,
        "severity": finding.severity,
        "finding_type": finding.finding_type,
        "message": finding.message,
        "location": finding.location or {},
        "expected": finding.expected,
        "actual": finding.actual,
        "basis": finding.basis,
        "suggestion": finding.suggestion,
        "status": finding.status,
        "evidence_ref": finding.evidence_ref,
    }


async def build_evidence_pack(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser,
) -> tuple[bytes, str]:
    project = await get_project(session, tenant_id, project_id)
    items, _ = await ensure_readiness(session, tenant_id, project_id)
    items = await refresh_auto_items(session, tenant_id, project_id)
    template = get_template(items[0].template_code if items else "it_integration")
    readiness = readiness_payload(project, template, items)

    decision: dict[str, Any] | None = None
    try:
        run = await latest_decision_card(session, tenant_id, project_id)
        decision = serialize_decision_run(
            run, include_inputs=has_permission(user.permissions, "project:approve")
        )
    except Exception:
        decision = None

    responses = await list_compliance_responses(session, tenant_id, project_id)
    responses_xlsx = build_responses_xlsx(project.name, responses["items"])

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
    findings: list[Any] = []
    if report is not None:
        rows = list(
            (
                await session.execute(
                    select(Finding)
                    .where(Finding.tenant_id == tenant_id, Finding.report_id == report.id)
                    .order_by(Finding.created_at)
                )
            ).scalars()
        )
        findings = await redact_findings_for_user(session, user, rows)
    compliance = {
        "report": {
            "id": str(report.id) if report else None,
            "status": report.status if report else None,
            "rule_version": report.rule_version if report else None,
            "summary": report.summary_json if report else None,
            "created_at": report.created_at.isoformat() if report else None,
        }
        if report
        else None,
        "findings": [_finding_dict(finding) for finding in findings],
    }

    sections = list(
        (
            await session.execute(
                select(BidSection)
                .where(
                    BidSection.tenant_id == tenant_id,
                    BidSection.project_id == project_id,
                )
                .order_by(BidSection.order_index)
            )
        ).scalars()
    )
    ai_usage = [
        {
            "section_id": str(section.id),
            "title": section.title,
            "generation_mode": section.generation_mode,
            "ai_meta": section.ai_meta or {},
            "factcheck_status": section.factcheck_status,
            "unsourced_count": section.unsourced_count,
            "approved_at": section.approved_at.isoformat() if section.approved_at else None,
        }
        for section in sections
        if section.ai_meta
    ]

    audit_rows = list(
        (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.tenant_id == tenant_id,
                    AuditEvent.resource_id == str(project_id),
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(500)
            )
        ).scalars()
    )
    audit = [
        {
            "seq": row.seq,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "data": row.data,
            "hash": row.hash,
            "created_at": row.created_at.isoformat(),
        }
        for row in audit_rows
    ]

    manifest = {
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": str(user.id),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "code": project.code,
            "buyer": project.buyer,
            "bid_deadline": project.bid_deadline.isoformat() if project.bid_deadline else None,
            "readiness_template": template["code"],
            "readiness_template_version": template["version"],
        },
        "contents": [
            "manifest.json",
            "decision-card.json",
            "readiness.json",
            "responses.xlsx",
            "compliance-report.json",
            "ai-usage.json",
            "audit-events.json",
        ],
        "notes": [
            "decision-card.json 的 inputs 仅在导出者具备 project:approve 时包含成本/报价。",
            "AI 使用记录来自 bid_sections.ai_meta；不包含章节正文。",
            "审计事件仅覆盖 resource_id 等于本项目的记录，最多 500 条。",
        ],
    }

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        archive.writestr(
            "decision-card.json",
            _json_bytes(decision if decision is not None else {"available": False}),
        )
        archive.writestr("readiness.json", _json_bytes(readiness))
        archive.writestr("responses.xlsx", responses_xlsx)
        archive.writestr("compliance-report.json", _json_bytes(compliance))
        archive.writestr("ai-usage.json", _json_bytes(ai_usage))
        archive.writestr("audit-events.json", _json_bytes(audit))
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", project.code or str(project.id))[:80]
    return buffer.getvalue(), f"evidence-pack-{safe_code}.zip"
