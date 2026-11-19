import re
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.permissions import has_permission
from app.db.locks import advisory_lock
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.generation.deps import get_llm_client
from app.modules.identity.deps import require_all_permissions, require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.commitments import list_commitments, snapshot_commitments
from app.modules.projects.decision_card import (
    compute_decision_card,
    confirm_decision_card,
    latest_decision_card,
    serialize_decision_run,
)
from app.modules.projects.defense import build_defense_docx, build_defense_outline
from app.modules.projects.delivery_summary import build_delivery_summary
from app.modules.projects.evidence_pack import build_evidence_pack
from app.modules.projects.export_branding import resolve_export_branding
from app.modules.projects.models import ScoringItem
from app.modules.projects.readiness import (
    ensure_readiness,
    get_template,
    list_templates,
    readiness_payload,
    refresh_auto_items,
    update_readiness_item,
)
from app.modules.projects.readiness_export import build_readiness_xlsx
from app.modules.projects.receipts import (
    ATTACHMENT_MAX_BYTES,
    attachment_disposition,
    attachment_payload,
    create_receipt,
    delete_attachment,
    list_receipt_payloads,
    read_attachment,
    receipt_payload,
    upload_attachment,
)
from app.modules.projects.responses_export import build_responses_xlsx
from app.modules.projects.results import get_result, record_result
from app.modules.projects.schemas import (
    BidCommitmentResponse,
    BidResultResponse,
    BidResultUpsert,
    CommitmentSnapshotRequest,
    ComplianceResponseBatch,
    ComplianceResponseBatchResult,
    ComplianceResponseItem,
    ComplianceResponseList,
    ComplianceResponseUpdate,
    ConstraintResponse,
    DecisionCardConfirm,
    DecisionCardRequest,
    DecisionCardResponse,
    DecisionResponse,
    DocumentDiffApply,
    DocumentResponse,
    MatrixResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectStatusChange,
    ProjectUpdate,
    ReadinessResponse,
    ReadinessUpdate,
    ScoringItemResponse,
    SubmissionReceiptAttachmentResponse,
    SubmissionReceiptCreate,
    SubmissionReceiptResponse,
)
from app.modules.projects.self_check import run_self_check
from app.modules.projects.service import (
    MAX_DOCUMENT_BYTES,
    apply_document_diff,
    batch_update_compliance_responses,
    build_compliance_responses,
    build_matrix,
    change_project_status,
    create_project,
    document_version_diff,
    get_latest_constraint,
    get_matrix,
    get_project,
    list_compliance_responses,
    list_documents,
    list_projects,
    make_decision,
    qualification_check,
    run_parse_job,
    update_compliance_response,
    update_project,
    upload_document,
)
from app.modules.tender.deps import get_ocr_provider
from app.modules.tender.ocr import OcrProvider

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    action: str,
    resource_id: str,
    data: dict,
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type="bid_project",
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.post("", response_model=ProjectResponse, status_code=201)
async def post_project(
    body: ProjectCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = await create_project(session, tenant_id=user.tenant_id, user_id=user.id, body=body)
    await _audit(session, user, "project.create", str(project.id), {"name": project.name})
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def patch_project(
    project_id: UUID,
    body: ProjectUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = await update_project(
        session, tenant_id=user.tenant_id, project_id=project_id, body=body
    )
    await _audit(session, user, "project.update", str(project.id), {"name": project.name})
    return ProjectResponse.model_validate(project)


@router.post("/{project_id}/status", response_model=ProjectResponse)
async def post_project_status(
    project_id: UUID,
    body: ProjectStatusChange,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """项目状态流转（在投 / 已中标 / 未中标）。

    未中标必须写原因，并落审计——复盘和「同类项目要不要再投」都靠这个。
    """
    before = await get_project(session, user.tenant_id, project_id)
    previous = before.status
    project = await change_project_status(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        new_status=body.status,
        reason=body.reason,
    )
    await _audit(
        session,
        user,
        "project.status.change",
        str(project.id),
        {"from": previous, "to": project.status, "reason": body.reason},
    )
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse])
async def get_projects(
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResponse]:
    projects = await list_projects(session, user.tenant_id)
    return [ProjectResponse.model_validate(project) for project in projects]


@router.get("/{project_id}")
async def get_project_detail(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await get_project(session, user.tenant_id, project_id)
    constraint = await get_latest_constraint(session, user.tenant_id, project_id)
    documents = await list_documents(session, user.tenant_id, project_id)
    scoring_item_count = 0
    if constraint is not None:
        scoring_item_count = len(
            (
                await session.execute(
                    select(ScoringItem.id).where(
                        ScoringItem.tenant_id == user.tenant_id,
                        ScoringItem.constraint_id == constraint.id,
                    )
                )
            ).scalars().all()
        )
    return {
        "project": ProjectResponse.model_validate(project),
        "latest_constraint": ConstraintResponse.model_validate(constraint) if constraint else None,
        "documents": [DocumentResponse.model_validate(document) for document in documents],
        "scoring_item_count": scoring_item_count,
    }


@router.post("/{project_id}/documents")
async def post_document(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_kind: str = Form("tender"),
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    vision_client: OcrProvider | None = Depends(get_ocr_provider),
) -> dict:
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValidationAppError("文件大小超过 20MB 上限")
    # 约束集版本号是「查最大值 +1」，并发上传会算出同一个 version 并撞上
    # uq_tender_constraints_project_version，把 IntegrityError 抛成 500。
    # 按项目串行化上传即可（同一项目本来也不该并行传两份招标文件）。
    async with advisory_lock(f"project-upload:{project_id}") as acquired:
        if not acquired:
            raise ConflictError("该项目正在上传文档，请稍后重试")
        document, constraint, parse_job = await upload_document(
            session,
            tenant_id=user.tenant_id,
            project_id=project_id,
            filename=file.filename or "tender",
            content_type=file.content_type,
            content=content,
            vision_client=vision_client,
            user_id=user.id,
            document_kind=document_kind,
        )
    if parse_job is not None:
        background_tasks.add_task(run_parse_job, parse_job.id, vision_client)
    await _audit(
        session,
        user,
        "project.document.upload",
        str(project_id),
        {
            "filename": document.filename,
            "document_kind": document.document_kind,
            "parse_status": document.parse_status,
            "ocr_used": document.ocr_used,
            "constraint_version": constraint.version if constraint else None,
        },
    )
    return {
        "document": DocumentResponse.model_validate(document),
        "constraint": ConstraintResponse.model_validate(constraint) if constraint else None,
        "parse_job": {"id": str(parse_job.id), "status": parse_job.status} if parse_job else None,
    }


@router.get("/{project_id}/documents", response_model=list[DocumentResponse])
async def get_documents(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentResponse]:
    documents = await list_documents(session, user.tenant_id, project_id)
    return [DocumentResponse.model_validate(document) for document in documents]


@router.get("/{project_id}/constraints", response_model=ConstraintResponse)
async def get_constraints(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ConstraintResponse:
    constraint = await get_latest_constraint(session, user.tenant_id, project_id)
    if constraint is None:
        raise NotFoundError("约束集不存在")
    return ConstraintResponse.model_validate(constraint)


@router.get("/{project_id}/scoring-items", response_model=list[ScoringItemResponse])
async def get_scoring_items(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ScoringItemResponse]:
    constraint = await get_latest_constraint(session, user.tenant_id, project_id)
    if constraint is None:
        return []
    items = (
        await session.execute(
            select(ScoringItem)
            .where(
                ScoringItem.tenant_id == user.tenant_id,
                ScoringItem.constraint_id == constraint.id,
            )
            .order_by(ScoringItem.created_at)
        )
    ).scalars().all()
    return [ScoringItemResponse.model_validate(item) for item in items]


@router.post("/{project_id}/matrix", response_model=list[MatrixResponse])
async def post_matrix(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> list[MatrixResponse]:
    rows = await build_matrix(session, user.tenant_id, project_id, user=user)
    await _audit(session, user, "project.matrix.build", str(project_id), {"rows": len(rows)})
    return rows


@router.get("/{project_id}/matrix", response_model=list[MatrixResponse])
async def get_matrix_rows(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[MatrixResponse]:
    rows = await get_matrix(session, user.tenant_id, project_id)
    return [MatrixResponse(**row) for row in rows]


@router.post("/{project_id}/qualification-check")
async def post_qualification_check(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    items, summary = await qualification_check(
        session, user.tenant_id, project_id, user=user
    )
    return {"items": items, "summary": summary}


@router.post("/{project_id}/decision")
async def post_decision(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    decision, qualifications, summary = await make_decision(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        user=user,
    )
    await _audit(session, user, "project.decision", str(project_id), {"status": decision.status})
    return {"decision": decision, "qualifications": qualifications, "summary": summary}


@router.get("/{project_id}/decision", response_model=DecisionResponse)
async def get_decision(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> DecisionResponse:
    project = await get_project(session, user.tenant_id, project_id)
    if not project.decision_status or not project.decision_json:
        raise NotFoundError("决策记录不存在")
    return DecisionResponse(
        status=project.decision_status,
        summary=str(project.decision_json.get("summary", "")),
        factors=list(project.decision_json.get("factors", [])),
    )


@router.put("/{project_id}/result", response_model=BidResultResponse)
async def put_result(
    project_id: UUID,
    body: BidResultUpsert,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> BidResultResponse:
    row = await record_result(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        body=body,
    )
    await _audit(
        session,
        user,
        "project.result.record",
        str(project_id),
        {
            "outcome": body.outcome,
            "win_amount": str(body.win_amount) if body.win_amount is not None else None,
            "loss_reason": body.loss_reason,
        },
    )
    return BidResultResponse.model_validate(row)


@router.get("/{project_id}/result", response_model=BidResultResponse)
async def get_project_result(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> BidResultResponse:
    row = await get_result(session, user.tenant_id, project_id)
    if row is None:
        raise NotFoundError("复盘记录不存在")
    response = BidResultResponse.model_validate(row)
    if not _can_view_decision_inputs(user) and response.win_amount is not None:
        response = response.model_copy(update={"win_amount": None})
    return response


@router.post("/{project_id}/responses/build", response_model=ComplianceResponseList)
async def post_build_responses(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ComplianceResponseList:
    rows = await build_compliance_responses(session, user.tenant_id, project_id)
    await _audit(
        session, user, "project.responses.build", str(project_id), {"count": len(rows)}
    )
    data = await list_compliance_responses(session, user.tenant_id, project_id)
    return ComplianceResponseList.model_validate(data)


@router.get("/{project_id}/responses", response_model=ComplianceResponseList)
async def get_responses(
    project_id: UUID,
    constraint_version: int | None = Query(default=None, ge=1),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ComplianceResponseList:
    data = await list_compliance_responses(
        session, user.tenant_id, project_id, constraint_version=constraint_version
    )
    return ComplianceResponseList.model_validate(data)


@router.get("/{project_id}/responses/export")
async def export_responses(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    project = await get_project(session, user.tenant_id, project_id)
    data = await list_compliance_responses(session, user.tenant_id, project_id)
    branding = await resolve_export_branding(session, user.tenant_id, project_id)
    content = build_responses_xlsx(project.name, data["items"], branding=branding)
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", project.code or str(project_id))[:80]
    filename = f"compliance-responses-{safe_code}.xlsx"
    await _audit(
        session,
        user,
        "project.responses.export",
        str(project_id),
        {"count": data["total"], "brand": branding.name, "brand_logo": branding.has_logo},
    )
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{project_id}/responses/batch", response_model=ComplianceResponseBatchResult)
async def post_responses_batch(
    project_id: UUID,
    body: ComplianceResponseBatch,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ComplianceResponseBatchResult:
    updated = await batch_update_compliance_responses(
        session,
        user.tenant_id,
        project_id,
        response_ids=body.response_ids,
        status=body.response_status,
        note=body.response_note,
        user_id=user.id,
    )
    await _audit(
        session,
        user,
        "project.responses.batch",
        str(project_id),
        {"count": updated, "status": body.response_status},
    )
    data = await list_compliance_responses(session, user.tenant_id, project_id)
    return ComplianceResponseBatchResult(
        updated=updated, summary=data["summary"], unresolved=data["unresolved"]
    )


@router.patch("/{project_id}/responses/{response_id}", response_model=ComplianceResponseItem)
async def patch_response(
    project_id: UUID,
    response_id: UUID,
    body: ComplianceResponseUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ComplianceResponseItem:
    row = await update_compliance_response(
        session,
        user.tenant_id,
        project_id,
        response_id,
        status=body.response_status,
        note=body.response_note,
        evidence_refs=body.evidence_refs,
        section_ref=body.section_ref,
        user_id=user.id,
    )
    await _audit(
        session,
        user,
        "project.responses.update",
        str(response_id),
        {"status": body.response_status},
    )
    return ComplianceResponseItem(
        id=row.id,
        requirement_type=row.requirement_type,
        requirement_text=row.requirement_text,
        category=row.category,
        response_status=row.response_status,
        response_note=row.response_note,
        evidence_refs=row.evidence_refs,
        section_ref=row.section_ref,
        source_span=row.source_span,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
    )


@router.get("/{project_id}/document-diff")
async def get_document_diff(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """7.2 Work 身份：最近两版招标文件的约束集增删改报告（requirement_key 稳定，
    同文本已确认状态存活；变更项对应响应与章节需人工复核）。"""
    return await document_version_diff(session, tenant_id=user.tenant_id, project_id=project_id)


def _can_view_decision_inputs(user: CurrentUser) -> bool:
    return has_permission(user.permissions, "project:approve")


@router.get("/{project_id}/decision-card", response_model=DecisionCardResponse)
async def get_decision_card(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> DecisionCardResponse:
    run = await latest_decision_card(session, user.tenant_id, project_id)
    return DecisionCardResponse.model_validate(
        serialize_decision_run(run, include_inputs=_can_view_decision_inputs(user))
    )


@router.post("/{project_id}/decision-card", response_model=DecisionCardResponse)
async def post_decision_card(
    project_id: UUID,
    body: DecisionCardRequest,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> DecisionCardResponse:
    run = await compute_decision_card(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user=user,
        inputs=body.inputs.model_dump(),
    )
    await _audit(
        session,
        user,
        "project.decision_card.compute",
        str(project_id),
        {"run_no": run.run_no, "status": run.computed_status, "score": float(run.score or 0)},
    )
    return DecisionCardResponse.model_validate(
        serialize_decision_run(run, include_inputs=_can_view_decision_inputs(user))
    )


@router.patch("/{project_id}/decision-card", response_model=DecisionCardResponse)
async def patch_decision_card(
    project_id: UUID,
    body: DecisionCardConfirm,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> DecisionCardResponse:
    latest = await latest_decision_card(session, user.tenant_id, project_id)
    run = await confirm_decision_card(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        run_id=latest.id,
        user=user,
        human_status=body.human_status,
        override_reason=body.override_reason,
    )
    await _audit(
        session,
        user,
        "project.decision_card.confirm",
        str(project_id),
        {"run_no": run.run_no, "status": run.human_status, "reason": body.override_reason},
    )
    return DecisionCardResponse.model_validate(
        serialize_decision_run(run, include_inputs=_can_view_decision_inputs(user))
    )


@router.get("/{project_id}/decision-card/runs", response_model=list[DecisionCardResponse])
async def get_decision_card_runs(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionCardResponse]:
    from app.modules.projects.models import BidDecisionRun

    await get_project(session, user.tenant_id, project_id)
    runs = list(
        (
            await session.execute(
                select(BidDecisionRun)
                .where(
                    BidDecisionRun.tenant_id == user.tenant_id,
                    BidDecisionRun.project_id == project_id,
                )
                .order_by(BidDecisionRun.run_no.desc())
                .limit(50)
            )
        ).scalars()
    )
    include_inputs = _can_view_decision_inputs(user)
    return [
        DecisionCardResponse.model_validate(
            serialize_decision_run(run, include_inputs=include_inputs)
        )
        for run in runs
    ]


@router.get("/{project_id}/self-check")
async def get_self_check(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await run_self_check(session, tenant_id=user.tenant_id, project_id=project_id)


@router.get("/{project_id}/defense-outline")
async def get_defense_outline(
    project_id: UUID,
    minutes: int = Query(default=20, ge=5, le=60),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await build_defense_outline(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        minutes=minutes,
        include_amounts=_can_view_decision_inputs(user),
    )


@router.get("/{project_id}/defense-outline/export")
async def export_defense_outline(
    project_id: UUID,
    minutes: int = Query(default=20, ge=5, le=60),
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    outline = await build_defense_outline(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        minutes=minutes,
        include_amounts=_can_view_decision_inputs(user),
    )
    content = build_defense_docx(outline)
    await _audit(
        session,
        user,
        "project.defense_outline.export",
        str(project_id),
        {"slides": len(outline.get("slides") or [])},
    )
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", str(project_id))[:80]
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="defense-{safe_code}.docx"'},
    )


@router.get("/{project_id}/commitments", response_model=list[BidCommitmentResponse])
async def get_commitments(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[BidCommitmentResponse]:
    rows = await list_commitments(session, user.tenant_id, project_id)
    can_view_amounts = _can_view_decision_inputs(user)
    responses: list[BidCommitmentResponse] = []
    for row in rows:
        item = BidCommitmentResponse.model_validate(row)
        if not can_view_amounts and item.kind == "price":
            item = item.model_copy(
                update={"value_amount": None, "value_text": "需项目审批权限查看报价"}
            )
        responses.append(item)
    return responses


@router.post("/{project_id}/commitments/snapshot", response_model=list[BidCommitmentResponse])
async def post_commitments_snapshot(
    project_id: UUID,
    body: CommitmentSnapshotRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> list[BidCommitmentResponse]:
    body = body or CommitmentSnapshotRequest()
    rows = await snapshot_commitments(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user=user,
        quote_amount=body.quote_amount,
        duration_days=body.duration_days,
        warranty_days=body.warranty_days,
        personnel=body.personnel,
    )
    await _audit(
        session,
        user,
        "project.commitments.snapshot",
        str(project_id),
        {"count": len(rows)},
    )
    return [BidCommitmentResponse.model_validate(row) for row in rows]


@router.get("/{project_id}/evidence-pack")
async def export_evidence_pack(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    content, filename = await build_evidence_pack(
        session, tenant_id=user.tenant_id, project_id=project_id, user=user
    )
    await _audit(
        session,
        user,
        "project.evidence_pack.export",
        str(project_id),
        {"bytes": len(content), "filename": filename},
    )
    return StreamingResponse(
        BytesIO(content),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/delivery-summary")
async def get_delivery_summary(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await build_delivery_summary(session, user.tenant_id, project_id)


@router.get("/{project_id}/submission-receipts", response_model=list[SubmissionReceiptResponse])
async def get_submission_receipts(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await list_receipt_payloads(session, user.tenant_id, project_id)


@router.post(
    "/{project_id}/submission-receipts",
    response_model=SubmissionReceiptResponse,
    status_code=201,
)
async def post_submission_receipt(
    project_id: UUID,
    body: SubmissionReceiptCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    receipt = await create_receipt(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        user_id=user.id,
        body=body,
    )
    await _audit(
        session,
        user,
        "project.submission_receipt.create",
        str(project_id),
        {
            "receipt_id": str(receipt.id),
            "platform": receipt.platform,
            "receipt_no": receipt.receipt_no,
            "checklist_summary": (receipt.checklist_snapshot or {}).get("summary"),
        },
    )
    return receipt_payload(receipt)


@router.post(
    "/{project_id}/submission-receipts/{receipt_id}/attachments",
    response_model=SubmissionReceiptAttachmentResponse,
    status_code=201,
)
async def post_submission_receipt_attachment(
    project_id: UUID,
    receipt_id: UUID,
    file: UploadFile = File(...),
    kind: str = Form("receipt"),
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # 多读 1 字节即可判断是否超限，避免把超大文件整个读进内存。
    content = await file.read(ATTACHMENT_MAX_BYTES + 1)
    attachment = await upload_attachment(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        receipt_id=receipt_id,
        user_id=user.id,
        kind=(kind or "").strip(),
        filename=file.filename,
        content_type=file.content_type,
        content=content,
    )
    await _audit(
        session,
        user,
        "project.submission_receipt.attachment.upload",
        str(project_id),
        {
            "receipt_id": str(receipt_id),
            "attachment_id": str(attachment.id),
            "kind": attachment.kind,
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
            "sha256": attachment.sha256,
        },
    )
    return attachment_payload(attachment)


@router.get("/{project_id}/submission-receipts/{receipt_id}/attachments/{attachment_id}")
async def download_submission_receipt_attachment(
    project_id: UUID,
    receipt_id: UUID,
    attachment_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    attachment, content = await read_attachment(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        receipt_id=receipt_id,
        attachment_id=attachment_id,
    )
    return StreamingResponse(
        BytesIO(content),
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": attachment_disposition(attachment.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{project_id}/submission-receipts/{receipt_id}/attachments/{attachment_id}",
    status_code=204,
)
async def delete_submission_receipt_attachment(
    project_id: UUID,
    receipt_id: UUID,
    attachment_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    attachment = await delete_attachment(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        receipt_id=receipt_id,
        attachment_id=attachment_id,
    )
    await _audit(
        session,
        user,
        "project.submission_receipt.attachment.delete",
        str(project_id),
        {
            "receipt_id": str(receipt_id),
            "attachment_id": str(attachment_id),
            "kind": attachment.kind,
            "filename": attachment.filename,
            "sha256": attachment.sha256,
        },
    )


@router.get("/{project_id}/readiness/templates")
async def get_readiness_templates(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    await get_project(session, user.tenant_id, project_id)
    return list_templates()


@router.get("/{project_id}/readiness", response_model=ReadinessResponse)
async def get_readiness(
    project_id: UUID,
    template_code: str | None = Query(default=None),
    refresh: bool = Query(default=True),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ReadinessResponse:
    items, created = await ensure_readiness(
        session, user.tenant_id, project_id, template_code=template_code
    )
    if created:
        await _audit(
            session,
            user,
            "project.readiness.init",
            str(project_id),
            {
                "template_code": items[0].template_code if items else template_code,
                "items": len(items),
            },
        )
    if refresh:
        items = await refresh_auto_items(session, user.tenant_id, project_id)
    project = await get_project(session, user.tenant_id, project_id)
    template = get_template(items[0].template_code if items else (template_code or "it_integration"))
    return ReadinessResponse.model_validate(readiness_payload(project, template, items))


@router.post("/{project_id}/readiness/refresh", response_model=ReadinessResponse)
async def post_readiness_refresh(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ReadinessResponse:
    items, created = await ensure_readiness(session, user.tenant_id, project_id)
    if created:
        await _audit(
            session,
            user,
            "project.readiness.init",
            str(project_id),
            {"template_code": items[0].template_code if items else None, "items": len(items)},
        )
    items = await refresh_auto_items(session, user.tenant_id, project_id)
    await _audit(
        session,
        user,
        "project.readiness.refresh",
        str(project_id),
        {"items": len(items)},
    )
    project = await get_project(session, user.tenant_id, project_id)
    template = get_template(items[0].template_code if items else "it_integration")
    return ReadinessResponse.model_validate(readiness_payload(project, template, items))


@router.get("/{project_id}/readiness/export")
async def export_readiness(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:export")),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    project = await get_project(session, user.tenant_id, project_id)
    items, _ = await ensure_readiness(session, user.tenant_id, project_id)
    items = await refresh_auto_items(session, user.tenant_id, project_id)
    template = get_template(items[0].template_code if items else "it_integration")
    payload = readiness_payload(project, template, items)
    branding = await resolve_export_branding(session, user.tenant_id, project_id)
    content = build_readiness_xlsx(project.name, payload, branding=branding)
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", project.code or str(project_id))[:80]
    filename = f"readiness-{safe_code}.xlsx"
    await _audit(
        session,
        user,
        "project.readiness.export",
        str(project_id),
        {"items": len(items), "brand": branding.name, "brand_logo": branding.has_logo},
    )
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{project_id}/readiness/{item_id}", response_model=dict)
async def patch_readiness_item(
    project_id: UUID,
    item_id: UUID,
    body: ReadinessUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    item = await update_readiness_item(
        session,
        user.tenant_id,
        project_id,
        item_id,
        user_id=user.id,
        status=body.status,
        note=body.note,
        owner_id=body.owner_id,
        collaborator_ids=body.collaborator_ids,
        due_at=body.due_at,
        evidence_refs=body.evidence_refs,
        waived_reason=body.waived_reason,
    )
    await _audit(
        session,
        user,
        "project.readiness.update",
        str(item.id),
        {"status": item.status, "blocking": item.is_blocking},
    )
    return {
        "id": str(item.id),
        "status": item.status,
        "owner_id": str(item.owner_id) if item.owner_id else None,
        "note": item.note,
        "evidence_refs": item.evidence_refs,
        "waived_reason": item.waived_reason,
        "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
    }


@router.post("/{project_id}/document-diff/apply")
async def post_apply_document_diff(
    project_id: UUID,
    body: DocumentDiffApply,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    result = await apply_document_diff(
        session,
        tenant_id=user.tenant_id,
        project_id=project_id,
        confirm=body.confirm,
        user_id=user.id,
        note=body.note,
    )
    await _audit(
        session,
        user,
        "project.document_diff.apply",
        str(project_id),
        {
            "constraint_version": result.get("constraint_version"),
            "responses": result.get("responses"),
            "note": body.note,
        },
    )
    return result


@router.get("/parse-jobs/{job_id}")
async def get_parse_job(
    job_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    from app.db.repository import TenantRepository
    from app.modules.projects.models import ParseJob

    class _JobLookup(TenantRepository[ParseJob]):
        model = ParseJob

    job = (
        await session.execute(
            _JobLookup(session, user.tenant_id)
            .scoped(select(ParseJob))
            .where(ParseJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError()
    return {
        "id": str(job.id),
        "project_id": str(job.project_id),
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
    }


class PolicyQaRequest(BaseModel):
    question: str


@router.post("/{project_id}/policy-qa")
async def post_policy_qa(
    project_id: UUID,
    body: PolicyQaRequest,
    user: CurrentUser = Depends(
        require_all_permissions("project:read", "knowledge:use")
    ),
    session: AsyncSession = Depends(get_session),
    llm_client=Depends(get_llm_client),
) -> dict[str, object]:
    """7.1 制度口径问答：知识库加权召回 + LLM 引用作答，无来源不编造。"""
    from app.modules.knowledge.kb import search as kb_search

    question = body.question.strip()
    if not question:
        raise ValidationAppError("问题不能为空")
    results = await kb_search(session, user, question, limit=4, action="use")
    chunks = results.get("chunks") or []
    if not chunks:
        return {
            "answer": "知识库中未找到与该问题相关的制度依据，不予生成回答（避免无据作答）。",
            "sources": [],
        }
    context = "\n\n".join(
        f"[{index + 1}] {hit['title']}\n{str(hit['chunk'])[:600]}"
        for index, hit in enumerate(chunks)
    )
    system = (
        "你是企业制度口径问答器。只依据给出的制度片段回答，每个结论必须标注 [n] 来源编号；"
        "片段不足以回答时，明确说明知识库依据不足，不要编造。"
    )
    answer = await llm_client.complete(system=system, user=f"制度片段：\n{context}\n\n问题：{question}")
    sources = [
        {
            "index": index + 1,
            "title": hit["title"],
            "excerpt": str(hit["chunk"])[:200],
            "document_id": str(hit["document_id"]),
        }
        for index, hit in enumerate(chunks)
    ]
    await _audit(
        session,
        user,
        "project.policy_qa",
        str(project_id),
        {"question": question[:200], "sources": len(sources)},
    )
    await session.commit()
    return {"answer": answer, "sources": sources}
