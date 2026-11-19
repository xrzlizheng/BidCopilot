import hashlib
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.assets.models import FieldPolicy
from app.modules.assets.repository import CompanyRepository
from app.modules.compliance.engine import ComplianceContext, evaluate
from app.modules.compliance.export import build_report_pdf, build_report_xlsx
from app.modules.compliance.models import ComplianceReport, Finding
from app.modules.compliance.policies import effective_rules_for_tenant
from app.modules.generation.models import BidSection, EvidenceLink
from app.modules.generation.service import build_docx_document
from app.modules.identity.models import Tenant
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import (
    CLEARANCE_RANK,
    accessible_kb_ids,
    filter_documents,
)
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeImage
from app.modules.projects.export_branding import branding_for_project
from app.modules.projects.models import ComplianceResponse, TenderDocument
from app.modules.projects.service import (
    get_latest_constraint,
    get_project,
    load_assets_for_evaluation,
    qualification_check,
)
from app.modules.system.models import TenantSetting


async def load_accessible_kb_texts(
    session: AsyncSession,
    tenant_id: UUID,
    user: CurrentUser | None,
    limit: int = 500,
) -> list[str]:
    """合规相似度只用当前用户可读的知识库/文档，避免跨部门/密级泄露。"""
    if user is None:
        return []
    kb_ids = await accessible_kb_ids(session, tenant_id, user, "read")
    if not kb_ids:
        return []
    documents = list(
        (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant_id,
                    KnowledgeDocument.kb_id.in_(kb_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    allowed = await filter_documents(session, documents, user, "read")
    allowed_ids = {document.id for document in allowed}
    if not allowed_ids:
        return []
    chunks = (
        await session.execute(
            select(KnowledgeChunk.text)
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.document_id.in_(allowed_ids),
                KnowledgeChunk.text != "",
            )
            .limit(limit)
        )
    ).scalars().all()
    return [str(text) for text in chunks if text]


def _docx_text(document: Any) -> str:
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    for section in document.sections:
        parts.extend(paragraph.text for paragraph in section.header.paragraphs)
        parts.extend(paragraph.text for paragraph in section.footer.paragraphs)
    return "\n".join(part for part in parts if part)


async def scan_project(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    user: CurrentUser | None = None,
) -> tuple[ComplianceReport, list[Finding]]:
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        raise ValidationAppError("请先上传并解析招标文件，再运行合规预检")
    sections = list(
        (
            await session.execute(
                select(BidSection)
                .where(BidSection.tenant_id == tenant_id, BidSection.project_id == project_id)
                .order_by(BidSection.order_index)
            )
        )
        .scalars()
        .all()
    )
    if not sections:
        raise ValidationAppError("请先生成章节，再运行合规预检")

    payload = constraint.payload or {}
    assets = await load_assets_for_evaluation(session, tenant_id, project.company_id, user=user)
    qualification_results, _ = await qualification_check(
        session, tenant_id, project_id, user=user
    )
    unsourced_rows = (
        await session.execute(
            select(EvidenceLink, BidSection.title)
            .join(BidSection, BidSection.id == EvidenceLink.section_id)
            .where(
                EvidenceLink.tenant_id == tenant_id,
                EvidenceLink.project_id == project_id,
                EvidenceLink.status == "unsourced",
            )
        )
    ).all()
    unsourced_claims = [
        {
            "claim": link.claim,
            "claim_type": link.claim_type,
            "section_id": str(link.section_id),
            "section_title": title,
            "evidence_ref": str(link.id),
        }
        for link, title in unsourced_rows
    ]

    document = build_docx_document(project, sections)
    buffer = BytesIO()
    document.save(buffer)
    docx_bytes = buffer.getvalue()
    company_name = None
    if project.company_id:
        company = await CompanyRepository(session, tenant_id).get_or_404(project.company_id)
        company_name = company.name
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()

    rule_version, rules, policy_count = await effective_rules_for_tenant(session, tenant_id)
    image_refs: list[UUID] = []
    for section in sections:
        for block in section.content_json or []:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            try:
                image_refs.append(UUID(str(block.get("image_id"))))
            except (ValueError, TypeError):
                continue
    knowledge_images: dict[str, KnowledgeImage] = {}
    if image_refs:
        rows = (
            await session.execute(
                select(KnowledgeImage).where(
                    KnowledgeImage.tenant_id == tenant_id,
                    KnowledgeImage.id.in_(image_refs),
                )
            )
        ).scalars().all()
        knowledge_images = {str(row.id): row for row in rows}

    responses = list(
        (
            await session.execute(
                select(ComplianceResponse).where(
                    ComplianceResponse.tenant_id == tenant_id,
                    ComplianceResponse.project_id == project.id,
                    ComplianceResponse.constraint_id == constraint.id,
                )
            )
        ).scalars()
    )

    kb_texts = await load_accessible_kb_texts(session, tenant_id, user)

    cost_model_row = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "decision_cost_model",
            )
        )
    ).scalar_one_or_none()
    cost_model = dict(cost_model_row.value) if cost_model_row and isinstance(cost_model_row.value, dict) else {}

    final_doc = (
        await session.execute(
            select(TenderDocument)
            .where(
                TenderDocument.tenant_id == tenant_id,
                TenderDocument.project_id == project.id,
                TenderDocument.document_kind == "final",
            )
            .order_by(TenderDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    final_assembly = dict(final_doc.assembly_meta or {}) if final_doc else {}

    context = ComplianceContext(
        project=project,
        payload=payload,
        sections=sections,
        assets=assets,
        qualification_results=qualification_results,
        unsourced_claims=unsourced_claims,
        docx_document=document,
        docx_text=_docx_text(document),
        content_text="\n".join(f"{section.title}\n{section.content_text}" for section in sections),
        company_name=company_name,
        tenant_name=tenant.name if tenant else None,
        knowledge_images=knowledge_images,
        kb_texts=kb_texts,
        responses=responses,
        cost_model=cost_model,
        final_assembly=final_assembly,
    )
    finding_dicts = evaluate(rules, context)
    hard = sum(1 for finding in finding_dicts if finding["finding_type"] == "HARD")
    soft = len(finding_dicts) - hard
    by_category: dict[str, int] = {}
    for finding in finding_dicts:
        by_category[finding["category"]] = by_category.get(finding["category"], 0) + 1
    summary = {
        "hard": hard,
        "soft": soft,
        "findings": len(finding_dicts),
        "by_category": by_category,
        "rules_version": rule_version,
        "policy_overrides": policy_count,
    }
    report = ComplianceReport(
        tenant_id=tenant_id,
        project_id=project_id,
        status="completed",
        rule_version=rule_version,
        file_hash=hashlib.sha256(docx_bytes).hexdigest(),
        summary_json=summary,
        created_by=user_id,
    )
    session.add(report)
    await session.flush()
    findings = [
        Finding(
            tenant_id=tenant_id,
            project_id=project_id,
            report_id=report.id,
            **finding,
        )
        for finding in finding_dicts
    ]
    session.add_all(findings)
    await session.commit()
    await session.refresh(report)
    for row in findings:
        await session.refresh(row)
    return report, findings


async def list_reports(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> list[ComplianceReport]:
    await get_project(session, tenant_id, project_id)
    return list(
        (
            await session.execute(
                select(ComplianceReport)
                .where(
                    ComplianceReport.tenant_id == tenant_id,
                    ComplianceReport.project_id == project_id,
                )
                .order_by(ComplianceReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_report(
    session: AsyncSession, tenant_id: UUID, report_id: UUID
) -> tuple[ComplianceReport, list[Finding]]:
    report = (
        await session.execute(
            select(ComplianceReport).where(
                ComplianceReport.tenant_id == tenant_id,
                ComplianceReport.id == report_id,
            )
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError()
    findings = list(
        (
            await session.execute(
                select(Finding)
                .where(Finding.tenant_id == tenant_id, Finding.report_id == report.id)
                .order_by(Finding.created_at)
            )
        )
        .scalars()
        .all()
    )
    return report, findings


async def redact_findings_for_user(
    session: AsyncSession,
    user: CurrentUser,
    findings: list[Any],
) -> list[Any]:
    """低密级用户不看到受字段策略保护的资产细节；计算仍使用全量数据。"""
    if not findings:
        return findings
    policies = list(
        (
            await session.execute(
                select(FieldPolicy).where(FieldPolicy.tenant_id == user.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    user_rank = CLEARANCE_RANK.get(user.clearance, 0)
    hidden_kinds = {
        policy.resource_type
        for policy in policies
        if CLEARANCE_RANK.get(policy.min_clearance, 99) > user_rank
    }
    if not hidden_kinds:
        return findings

    redacted: list[Any] = []
    for finding in findings:
        location = finding.location if isinstance(finding.location, dict) else {}
        scope = str(location.get("scope") or "")
        kinds: set[str] = set()
        if scope in hidden_kinds:
            kinds.add(scope)
        if scope == "assets":
            kinds |= hidden_kinds
        if str(finding.rule_id).startswith("QUAL-"):
            kinds |= hidden_kinds & {"qualifications", "personnel", "performances", "credits"}
        if not kinds:
            redacted.append(finding)
            continue
        redacted.append(
            SimpleNamespace(
                id=finding.id,
                rule_id=finding.rule_id,
                category=finding.category,
                severity=finding.severity,
                finding_type=finding.finding_type,
                message="该发现涉及受权限保护的资产字段，需高权限复核。",
                location={"scope": scope or "assets", "redacted": True},
                expected=None,
                actual=None,
                basis=finding.basis,
                suggestion="请高权限用户复核资产材料后处理。",
                auto_fixable=False,
                needs_review=True,
                status=finding.status,
                evidence_ref=None,
                created_at=finding.created_at,
            )
        )
    return redacted


async def latest_report(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> tuple[ComplianceReport, list[Finding]]:
    reports = await list_reports(session, tenant_id, project_id)
    if not reports:
        raise NotFoundError("合规预检报告不存在")
    return await get_report(session, tenant_id, reports[0].id)


async def export_report(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    report_id: UUID,
    export_format: str,
    user: CurrentUser,
) -> tuple[bytes, str, dict[str, Any]]:
    report, findings = await get_report(session, tenant_id, report_id)
    findings = await redact_findings_for_user(session, user, findings)
    project = await get_project(session, tenant_id, report.project_id)
    # 品牌解析失败只降级为默认品牌，不能让报告导出失败（见 export_branding 的口径）。
    branding = await branding_for_project(session, tenant_id, project)
    if export_format == "xlsx":
        data = build_report_xlsx(report, findings, project.name, branding=branding)
        media = "xlsx"
    elif export_format == "pdf":
        data = build_report_pdf(report, findings, project.name, branding=branding)
        media = "pdf"
    else:
        raise ValidationAppError("不支持的导出格式")
    return data, f"compliance_{report.id}.{media}", branding.audit_fields()


async def update_finding(
    session: AsyncSession, tenant_id: UUID, finding_id: UUID, status: str
) -> Finding:
    finding = (
        await session.execute(
            select(Finding).where(Finding.tenant_id == tenant_id, Finding.id == finding_id)
        )
    ).scalar_one_or_none()
    if finding is None:
        raise NotFoundError()
    finding.status = status
    await session.commit()
    await session.refresh(finding)
    return finding
