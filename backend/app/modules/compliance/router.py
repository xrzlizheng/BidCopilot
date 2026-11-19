from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.compliance.policies import list_policy_rows, update_policy
from app.modules.compliance.schemas import (
    FindingResponse,
    FindingUpdate,
    PolicyResponse,
    PolicyUpdate,
    ReportExportRequest,
    ReportResponse,
    ReportSummaryResponse,
)
from app.modules.compliance.service import (
    export_report,
    get_report,
    latest_report,
    list_reports,
    redact_findings_for_user,
    scan_project,
    update_finding,
)
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications import webhooks

router = APIRouter(prefix="/api/v1", tags=["compliance"])


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    action: str,
    resource_type: str,
    resource_id: str,
    data: dict,
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.post("/projects/{project_id}/compliance/scan", response_model=ReportResponse)
async def post_scan(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    report, findings = await scan_project(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        user=user,
    )
    await _audit(
        session,
        user,
        "compliance.scan",
        "compliance_report",
        str(report.id),
        {"hard": report.summary_json.get("hard", 0), "soft": report.summary_json.get("soft", 0)},
    )
    # 合规严重问题事件：只有存在 HARD finding 才发，避免噪声。
    webhooks.notify_compliance_hard_findings(
        user.tenant_id,
        project_id=project_id,
        report_id=report.id,
        hard_count=int(report.summary_json.get("hard", 0)),
        rule_ids=sorted(
            {finding.rule_id for finding in findings if finding.finding_type == "HARD"}
        ),
    )
    findings = await redact_findings_for_user(session, user, findings)
    return ReportResponse(
        id=report.id,
        project_id=report.project_id,
        status=report.status,
        rule_version=report.rule_version,
        file_hash=report.file_hash,
        summary_json=report.summary_json,
        created_at=report.created_at,
        findings=[FindingResponse.model_validate(finding) for finding in findings],
    )


@router.get(
    "/projects/{project_id}/compliance/reports",
    response_model=list[ReportSummaryResponse],
)
async def get_reports(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ReportSummaryResponse]:
    reports = await list_reports(session, user.tenant_id, project_id)
    return [ReportSummaryResponse.model_validate(report) for report in reports]


@router.get("/projects/{project_id}/compliance/latest", response_model=ReportResponse)
async def get_latest(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    report, findings = await latest_report(session, user.tenant_id, project_id)
    findings = await redact_findings_for_user(session, user, findings)
    return ReportResponse(
        id=report.id,
        project_id=report.project_id,
        status=report.status,
        rule_version=report.rule_version,
        file_hash=report.file_hash,
        summary_json=report.summary_json,
        created_at=report.created_at,
        findings=[FindingResponse.model_validate(finding) for finding in findings],
    )


@router.get("/compliance/reports/{report_id}", response_model=ReportResponse)
async def get_report_detail(
    report_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    report, findings = await get_report(session, user.tenant_id, report_id)
    findings = await redact_findings_for_user(session, user, findings)
    return ReportResponse(
        id=report.id,
        project_id=report.project_id,
        status=report.status,
        rule_version=report.rule_version,
        file_hash=report.file_hash,
        summary_json=report.summary_json,
        created_at=report.created_at,
        findings=[FindingResponse.model_validate(finding) for finding in findings],
    )


@router.post("/compliance/reports/{report_id}/export")
async def post_report_export(
    report_id: UUID,
    body: ReportExportRequest,
    user: CurrentUser = Depends(require_permission("compliance:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    data, filename, brand_audit = await export_report(
        session,
        tenant_id=user.tenant_id,
        report_id=report_id,
        export_format=body.format,
        user=user,
    )
    await _audit(
        session,
        user,
        "compliance.report.export",
        "compliance_report",
        str(report_id),
        {"format": body.format, "filename": filename, **brand_audit},
    )
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if body.format == "xlsx"
        else "application/pdf"
    )
    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/findings/{finding_id}", response_model=FindingResponse)
async def patch_finding(
    finding_id: UUID,
    body: FindingUpdate,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> FindingResponse:
    finding = await update_finding(session, user.tenant_id, finding_id, body.status)
    await _audit(
        session,
        user,
        "finding.update",
        "finding",
        str(finding.id),
        {"status": finding.status},
    )
    redacted = await redact_findings_for_user(session, user, [finding])
    return FindingResponse.model_validate(redacted[0])


@router.get("/policies", response_model=list[PolicyResponse])
async def get_policies(
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await list_policy_rows(session, user.tenant_id)


@router.patch("/policies/{rule_id}", response_model=PolicyResponse)
async def patch_policy(
    rule_id: str,
    body: PolicyUpdate,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await update_policy(
        session,
        tenant_id=user.tenant_id,
        rule_id=rule_id,
        enabled=body.enabled,
        severity_override=body.severity_override,
        threshold_override=body.threshold_override,
        user_id=user.id,
    )
    await _audit(
        session,
        user,
        "compliance.policy.update",
        "policy",
        rule_id,
        {
            "enabled": body.enabled,
            "severity_override": body.severity_override,
            "threshold_override": body.threshold_override,
        },
    )
    rows = await list_policy_rows(session, user.tenant_id)
    return next(row for row in rows if row["rule_id"] == rule_id)
