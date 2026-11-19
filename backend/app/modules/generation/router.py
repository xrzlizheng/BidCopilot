from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.db.locks import advisory_lock
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.generation.deps import get_llm_client
from app.modules.generation.llm_client import LLMClient
from app.modules.generation.models import EvidenceLink
from app.modules.generation.policy_sources import load_policy_sources, verify_citation
from app.modules.generation.schemas import (
    ApproveRequest,
    EvidenceResponse,
    ExportRequest,
    GenerateRequest,
    GenerateResponse,
    SectionResponse,
    SectionUpdate,
)
from app.modules.generation.service import (
    approve_section,
    build_outline,
    export_docx,
    factcheck_section,
    generate_sections,
    get_section,
    list_sections,
    update_section,
)
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.export_branding import resolve_export_branding
from app.modules.projects.service import list_compliance_responses, unbuilt_response_item_count

router = APIRouter(prefix="/api/v1", tags=["generation"])


class PolicyVerifyRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=50)


@router.get("/policy-sources")
async def get_policy_sources(
    user: CurrentUser = Depends(require_permission("project:read")),
) -> dict:
    version, sources = load_policy_sources()
    return {
        "version": version,
        "sources": [
            {
                "id": source.id,
                "title": source.title,
                "issuer": source.issuer,
                "doc_no": source.doc_no,
                "date": source.date,
                "url": source.url,
                "note": source.note,
            }
            for source in sources
        ],
    }


@router.post("/policy-sources/verify")
async def post_policy_sources_verify(
    body: PolicyVerifyRequest,
    user: CurrentUser = Depends(require_permission("project:read")),
) -> dict:
    return {
        "results": [
            {"text": text[:500], **verify_citation(text)} for text in body.texts
        ]
    }


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


@router.post("/projects/{project_id}/outline", response_model=list[SectionResponse])
async def post_outline(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> list[SectionResponse]:
    sections = await build_outline(session, user.tenant_id, project_id)
    await _audit(session, user, "project.outline", "bid_project", str(project_id), {"sections": len(sections)})
    return [SectionResponse.model_validate(section) for section in sections]


@router.post("/projects/{project_id}/generate", response_model=GenerateResponse)
async def post_generate(
    project_id: UUID,
    body: GenerateRequest,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    llm_client: LLMClient = Depends(get_llm_client),
) -> GenerateResponse:
    # 同一项目的并发生成会重复调用付费模型（双击/双开标签页），按项目互斥
    async with advisory_lock(f"project-generation:{project_id}") as acquired:
        if not acquired:
            raise ConflictError("该项目的章节生成正在进行中，请稍后重试")
        run = await generate_sections(
            session,
            user=user,
            project_id=project_id,
            mode=body.mode,
            section_ids=body.section_ids,
            llm_client=llm_client,
        )
    await _audit(
        session,
        user,
        "project.generate",
        "bid_project",
        str(project_id),
        {"mode": body.mode, "generated": run.sections_generated},
    )
    return GenerateResponse(
        run_id=run.id,
        status=run.status,
        sections_total=run.sections_total,
        sections_generated=run.sections_generated,
        error_code=run.error_code,
        error_message=run.error_message,
    )


@router.get("/projects/{project_id}/sections", response_model=list[SectionResponse])
async def get_sections(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[SectionResponse]:
    sections = await list_sections(session, user.tenant_id, project_id)
    return [SectionResponse.model_validate(section) for section in sections]


@router.patch("/sections/{section_id}", response_model=SectionResponse)
async def patch_section(
    section_id: UUID,
    body: SectionUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> SectionResponse:
    section = await update_section(
        session,
        tenant_id=user.tenant_id,
        section_id=section_id,
        title=body.title,
        content_text=body.content_text,
        content_json=body.content_json,
    )
    await _audit(session, user, "section.update", "bid_section", str(section.id), {})
    return SectionResponse.model_validate(section)


@router.post("/sections/{section_id}/factcheck")
async def post_factcheck(
    section_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    section, evidence = await factcheck_section(
        session, user.tenant_id, section_id, user=user
    )
    return {
        "section": SectionResponse.model_validate(section),
        "evidence": [EvidenceResponse.model_validate(link) for link in evidence],
    }


@router.get("/sections/{section_id}/evidence", response_model=list[EvidenceResponse])
async def get_evidence(
    section_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[EvidenceResponse]:
    section = await get_section(session, user.tenant_id, section_id)
    links = (
        await session.execute(
            select(EvidenceLink)
            .where(
                EvidenceLink.tenant_id == user.tenant_id,
                EvidenceLink.section_id == section.id,
            )
            .order_by(EvidenceLink.created_at)
        )
    ).scalars().all()
    return [EvidenceResponse.model_validate(link) for link in links]


@router.post("/sections/{section_id}/approve", response_model=SectionResponse)
async def post_approve(
    section_id: UUID,
    body: ApproveRequest,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> SectionResponse:
    section = await approve_section(
        session,
        tenant_id=user.tenant_id,
        section_id=section_id,
        user_id=user.id,
        acknowledge_unsourced=body.acknowledge_unsourced,
    )
    await _audit(
        session,
        user,
        "section.approve",
        "bid_section",
        str(section.id),
        {"acknowledge_unsourced": body.acknowledge_unsourced},
    )
    return SectionResponse.model_validate(section)


@router.post("/projects/{project_id}/anonymize")
async def post_anonymize(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """6.2 一键匿名化：不经过导出闸门，直接产出脱敏 docx 供人工确认。

    与暗标导出共用同一脱敏路径（页眉页脚/正文/表格身份名替换 + 核心属性清空），
    但绕过"未处理响应条目"闸门——匿名化只是清洗动作，不代表可以出标。
    """
    data, filename, _ = await export_docx(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        dark_bid=True,
        user=user,
    )
    await _audit(
        session,
        user,
        "project.anonymize",
        "bid_project",
        str(project_id),
        {"filename": filename},
    )
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/{project_id}/export/docx")
async def post_export(
    project_id: UUID,
    body: ExportRequest,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    responses = await list_compliance_responses(session, user.tenant_id, project_id)
    unresolved = responses["unresolved"]
    unbuilt = 0
    if responses["total"] == 0:
        # 跳过"生成响应表"这一步不能成为绕过闸门的办法，否则漏答风险完全无声
        unbuilt = await unbuilt_response_item_count(session, user.tenant_id, project_id)
    if (unresolved or unbuilt) and not body.acknowledge_unresolved:
        if unbuilt:
            detail = f"尚未生成逐条响应表，招标条款共 {unbuilt} 条待逐条确认"
        else:
            detail = (
                f"逐条响应表仍有 {unresolved} 项未处理（待确认 {responses['summary']['pending']}、"
                f"偏离 {responses['summary']['deviated']}、不响应 {responses['summary']['not_responded']}）"
            )
        raise ConflictError(f"{detail}；处理后重试，或确认承担风险后强制导出")
    from app.modules.projects.service import get_latest_constraint

    constraint = await get_latest_constraint(session, user.tenant_id, project_id)
    # 暗标导出不带任何身份信息（含客户品牌）；明标才解析并嵌入客户品牌。
    branding = None if body.dark_bid else await resolve_export_branding(
        session, user.tenant_id, project_id
    )
    data, filename, format_findings = await export_docx(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        dark_bid=body.dark_bid,
        constraint_payload=constraint.payload if constraint else None,
        user=user,
        branding=branding,
    )
    await _audit(
        session,
        user,
        "project.export",
        "bid_project",
        str(project_id),
        {
            "dark_bid": body.dark_bid,
            "filename": filename,
            "forced_past_unresolved": bool(unresolved or unbuilt),
            "format_findings": format_findings,
            **(branding.audit_fields() if branding else {"brand": None, "brand_logo": False}),
        },
    )
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
