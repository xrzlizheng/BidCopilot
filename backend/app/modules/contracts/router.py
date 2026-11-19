import hashlib
import logging
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.core.spreadsheet import active_sheet, excel_row
from app.db.repository import TenantRepository
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.contracts.models import (
    ContractClause,
    ContractFinding,
    ContractMilestone,
    ContractReview,
    ContractReviewTemplate,
    ContractTemplateVersion,
    TenantContractRule,
)
from app.modules.contracts.schemas import (
    ContractReviewDetailResponse,
    ContractReviewResponse,
    ContractReviewTemplateResponse,
    ContractTemplateUpsert,
    ContractTemplateVersionResponse,
    FindingStatusUpdate,
)
from app.modules.contracts.service import (
    DISCLAIMER,
    MAX_CONTRACT_BYTES,
    apply_tenant_rules,
    create_review,
    deep_review_with_llm,
    diff_re_review,
    diff_supplement,
    extract_milestones,
    get_clauses,
    get_findings,
    get_review,
    list_reviews,
    load_review_templates,
    split_clauses,
)
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.generation.deps import get_llm_client
from app.modules.generation.llm_client import LLMClient
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.models import BidProject, TenderConstraint
from app.modules.tender.deps import get_ocr_provider
from app.modules.tender.ocr import IMAGE_SUFFIXES, OcrConfigError, OcrProvider
from app.modules.tender.parser import ScannedDocumentError, UnsupportedDocumentError


class _ConstraintLookup(TenantRepository[TenderConstraint]):
    model = TenderConstraint


class _FindingLookup(TenantRepository[ContractFinding]):
    model = ContractFinding


class _TenantRuleLookup(TenantRepository[TenantContractRule]):
    model = TenantContractRule


class _ProjectLookup(TenantRepository[BidProject]):
    model = BidProject


async def _require_project(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> None:
    """上传/复审写路径显式校验项目归属当前租户（跨租户返回 404，不泄露存在性）。"""
    project = (
        await session.execute(
            _ProjectLookup(session, tenant_id)
            .scoped(select(BidProject))
            .where(BidProject.id == project_id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError()


class _MilestoneLookup(TenantRepository[ContractMilestone]):
    model = ContractMilestone


class _TemplateLookup(TenantRepository[ContractReviewTemplate]):
    model = ContractReviewTemplate

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/contract-reviews", tags=["contracts"]
)

ALLOWED_SUFFIXES = {".docx", ".pdf", ".txt", ".md"} | IMAGE_SUFFIXES


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read(MAX_CONTRACT_BYTES + 1)
    if len(content) > MAX_CONTRACT_BYTES:
        raise ValidationAppError("合同文件超过 10MB 上限")
    return content


async def _latest_constraint_payload(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, Any] | None:
    constraint = (
        await session.execute(
            _ConstraintLookup(session, tenant_id)
            .scoped(select(TenderConstraint))
            .where(TenderConstraint.project_id == project_id)
            .order_by(TenderConstraint.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return constraint.payload if constraint is not None else None


def _detail(
    review, clauses, findings
) -> ContractReviewDetailResponse:
    base = ContractReviewDetailResponse(
        **ContractReviewResponse.model_validate(review).model_dump(),
        clauses=list(clauses),
        findings=list(findings),
        disclaimer=DISCLAIMER,
    )
    return base


@router.get("/review-templates", response_model=list[ContractReviewTemplateResponse])
async def get_contract_review_templates(
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ContractReviewTemplateResponse]:
    """审查点模板目录：系统模板 + 本租户模板（系统行不挂租户外键，不能走 scoped）。"""
    templates = await load_review_templates(session, user.tenant_id)
    return [ContractReviewTemplateResponse.model_validate(t) for t in templates]


# —— 租户审查点模板 CRUD + 版本快照（backlog B7）——
# 变更体路由必须注册在 GET /{review_id} 之前（同 tenant-rules 的顺序约束）。


def _template_snapshot(template: ContractReviewTemplate) -> dict[str, Any]:
    return {
        "contract_type": template.contract_type,
        "name": template.name,
        "contract_type_keywords": list(template.contract_type_keywords or []),
        "review_points": list(template.review_points or []),
        "core_purposes": list(template.core_purposes or []),
        "prompt_rules": list(template.prompt_rules or []),
        "report_sections": list(template.report_sections or []),
        "typical_description": template.typical_description,
        "enabled": template.enabled,
    }


async def _own_template(
    session: AsyncSession,
    tenant_id: UUID,
    template_id: UUID,
    *,
    for_update: bool = False,
) -> ContractReviewTemplate:
    """取本租户自有模板：scoped 过滤使系统行（tenant_id IS NULL）天然 404。

    for_update 用于写路径（PATCH/revert）：行锁串行化「快照 + 版本号 + 覆盖」，
    避免并发写把对方的版本从版本表中静默挤掉。
    """
    statement = (
        _TemplateLookup(session, tenant_id)
        .scoped(select(ContractReviewTemplate))
        .where(ContractReviewTemplate.id == template_id)
    )
    if for_update:
        statement = statement.with_for_update()
    template = (await session.execute(statement)).scalar_one_or_none()
    if template is None:
        raise NotFoundError()
    return template


async def _next_template_version(session: AsyncSession, template_id: UUID) -> int:
    current = (
        await session.execute(
            select(func.max(ContractTemplateVersion.version)).where(
                ContractTemplateVersion.template_id == template_id
            )
        )
    ).scalar_one_or_none()
    return (current or 0) + 1


def _template_integrity_message(exc: IntegrityError) -> str:
    """区分两类唯一约束冲突：版本快照竞态 vs contract_type 租户内重复。"""
    text = str(exc)
    if "contract_template_versions" in text:
        return "模板正被并发修改（版本快照冲突），请重试"
    return "本租户已存在相同 contract_type 的模板"


@router.post("/review-templates", response_model=ContractReviewTemplateResponse)
async def create_contract_review_template(
    payload: ContractTemplateUpsert,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewTemplateResponse:
    """创建租户审查点模板：首版快照 version=1，contract_type 租户内唯一。"""
    duplicate = (
        await session.execute(
            _TemplateLookup(session, user.tenant_id)
            .scoped(select(ContractReviewTemplate))
            .where(ContractReviewTemplate.contract_type == payload.contract_type)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError(f"本租户已存在 contract_type={payload.contract_type} 的模板")
    template = ContractReviewTemplate(
        tenant_id=user.tenant_id,
        is_system=False,
        contract_type=payload.contract_type,
        name=payload.name,
        contract_type_keywords=payload.contract_type_keywords,
        review_points=payload.review_points,
        core_purposes=payload.core_purposes,
        prompt_rules=payload.prompt_rules,
        report_sections=payload.report_sections,
        typical_description=payload.typical_description,
        enabled=payload.enabled,
    )
    session.add(template)
    try:
        await session.flush()
        session.add(
            ContractTemplateVersion(
                template_id=template.id,
                version=1,
                snapshot=_template_snapshot(template),
                created_by=user.id,
            )
        )
        await session.commit()
    except IntegrityError as exc:
        # 并发双建同 contract_type：预检与 commit 之间的竞窗（review-2 P2）
        await session.rollback()
        raise ConflictError(_template_integrity_message(exc)) from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.tenant_template_created",
        resource_type="contract_review_template",
        resource_id=str(template.id),
        data={"contract_type": template.contract_type},
    )
    await session.commit()
    return ContractReviewTemplateResponse.model_validate(template)


@router.patch("/review-templates/{template_id}", response_model=ContractReviewTemplateResponse)
async def update_contract_review_template(
    template_id: UUID,
    payload: ContractTemplateUpsert,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewTemplateResponse:
    """更新租户模板：变更前整体快照（version 递增），可回溯。"""
    template = await _own_template(session, user.tenant_id, template_id, for_update=True)
    session.add(
        ContractTemplateVersion(
            template_id=template.id,
            version=await _next_template_version(session, template.id),
            snapshot=_template_snapshot(template),
            created_by=user.id,
        )
    )
    template.contract_type = payload.contract_type
    template.name = payload.name
    template.contract_type_keywords = payload.contract_type_keywords
    template.review_points = payload.review_points
    template.core_purposes = payload.core_purposes
    template.prompt_rules = payload.prompt_rules
    template.report_sections = payload.report_sections
    template.typical_description = payload.typical_description
    template.enabled = payload.enabled
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(_template_integrity_message(exc)) from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.tenant_template_updated",
        resource_type="contract_review_template",
        resource_id=str(template.id),
        data={"contract_type": template.contract_type},
    )
    await session.commit()
    return ContractReviewTemplateResponse.model_validate(template)


@router.delete("/review-templates/{template_id}", response_model=ContractReviewTemplateResponse)
async def delete_contract_review_template(
    template_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewTemplateResponse:
    """删除租户模板（版本随 FK CASCADE 清理）；系统模板经 scoped 天然 404。"""
    template = await _own_template(session, user.tenant_id, template_id)
    response = ContractReviewTemplateResponse.model_validate(template)
    await session.delete(template)
    await session.commit()
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.tenant_template_deleted",
        resource_type="contract_review_template",
        resource_id=str(template_id),
        data={"contract_type": response.contract_type},
    )
    await session.commit()
    return response


@router.get(
    "/review-templates/{template_id}/versions",
    response_model=list[ContractTemplateVersionResponse],
)
async def list_contract_template_versions(
    template_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ContractTemplateVersionResponse]:
    await _own_template(session, user.tenant_id, template_id)
    rows = (
        (
            await session.execute(
                select(ContractTemplateVersion)
                .where(ContractTemplateVersion.template_id == template_id)
                .order_by(ContractTemplateVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ContractTemplateVersionResponse.model_validate(row) for row in rows]


@router.post(
    "/review-templates/{template_id}/revert/{version}",
    response_model=ContractReviewTemplateResponse,
)
async def revert_contract_review_template(
    template_id: UUID,
    version: int,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewTemplateResponse:
    """回退到指定版本：当前内容先快照为新版本，再按快照覆盖（回退可再回退）。"""
    template = await _own_template(session, user.tenant_id, template_id, for_update=True)
    snapshot = (
        await session.execute(
            select(ContractTemplateVersion).where(
                ContractTemplateVersion.template_id == template_id,
                ContractTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise NotFoundError()
    data = snapshot.snapshot

    def _str_list(value: Any) -> list[str]:
        # 快照仅由 _template_snapshot 服务端写入；对库外手改防御类型漂移（review-2 P3）
        return [str(item) for item in value] if isinstance(value, list) else []

    session.add(
        ContractTemplateVersion(
            template_id=template.id,
            version=await _next_template_version(session, template.id),
            snapshot=_template_snapshot(template),
            created_by=user.id,
        )
    )
    template.contract_type = str(data.get("contract_type") or template.contract_type)
    template.name = str(data.get("name") or template.name)
    template.contract_type_keywords = _str_list(data.get("contract_type_keywords"))
    template.review_points = _str_list(data.get("review_points"))
    template.core_purposes = _str_list(data.get("core_purposes"))
    template.prompt_rules = _str_list(data.get("prompt_rules"))
    template.report_sections = _str_list(data.get("report_sections"))
    template.typical_description = data.get("typical_description")
    template.enabled = bool(data.get("enabled", True))
    try:
        await session.commit()
    except IntegrityError as exc:
        # 路径 A：revert 恢复的 contract_type 已被其他模板占用；
        # 路径 B：并发 PATCH/revert 的版本快照竞态（review-2 P1）——都不允许 500
        await session.rollback()
        raise ConflictError(_template_integrity_message(exc)) from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.tenant_template_reverted",
        resource_type="contract_review_template",
        resource_id=str(template.id),
        data={"reverted_to": version},
    )
    await session.commit()
    return ContractReviewTemplateResponse.model_validate(template)


async def _attach_review_extras(
    session: AsyncSession,
    user: CurrentUser,
    review: ContractReview,
    clauses: list[ContractClause],
) -> None:
    """创建审查后的追加项：履约里程碑启发式抽取 + 租户自定义规则命中。"""
    parsed_clauses = [
        {
            "seq": clause.seq,
            "title": clause.title,
            "text": clause.text,
            "category": clause.category,
            "page": clause.page,
        }
        for clause in clauses
    ]
    for milestone in extract_milestones(parsed_clauses):
        session.add(
            ContractMilestone(
                tenant_id=user.tenant_id,
                review_id=review.id,
                kind=milestone["kind"],
                title=milestone["title"],
                due_at=milestone["due_at"],
                amount=milestone["amount"],
            )
        )
    tenant_rules = (
        (
            await session.execute(
                _TenantRuleLookup(session, user.tenant_id).scoped(select(TenantContractRule))
            )
        )
        .scalars()
        .all()
    )
    tenant_findings = apply_tenant_rules(parsed_clauses, list(tenant_rules))
    clause_by_seq = {clause.seq: clause for clause in clauses}
    for data in tenant_findings:
        session.add(
            ContractFinding(
                tenant_id=user.tenant_id,
                review_id=review.id,
                clause_id=clause_by_seq[data["clause_id_seq"]].id if data.get("clause_id_seq") else None,
                kind=data["kind"],
                rule_id=data["rule_id"],
                severity=data["severity"],
                message=data["message"],
                legal_basis=data.get("legal_basis"),
                evidence_span=data.get("evidence_span") or {},
                source="rule",
                confidence=Decimal("1.00"),
                detection_method="rule",
            )
        )


def _parse_template_overrides(
    template_ids_csv: str, review_points_text: str, core_purpose: str
) -> dict[str, Any]:
    """上传表单的可选模板参数：CSV 模板 ID + 分行/分号审查点 + 核心目的。

    文本逐字进 system prompt 并持久化进 template_matches JSONB，必须限长
    （review P2-3）：单条 ≤500 字，总量 ≤4000 字。
    """
    template_ids: list[str] = []
    for piece in (template_ids_csv or "").split(","):
        tid = piece.strip()
        if not tid:
            continue
        try:
            template_ids.append(str(UUID(tid)))
        except ValueError as exc:
            raise ValidationAppError(f"审查点模板 ID 不合法：{tid}") from exc
    extra_points: list[str] = []
    for point in re.split(r"[\n;；]", review_points_text or ""):
        cleaned = point.strip()[:500]
        if not cleaned:
            continue
        if len(extra_points) >= 20:
            break
        if sum(len(item) for item in extra_points) + len(cleaned) > 4000:
            break
        extra_points.append(cleaned)
    overrides: dict[str, Any] = {}
    if template_ids:
        overrides["template_ids"] = template_ids
    if extra_points:
        overrides["extra_review_points"] = extra_points
    if (core_purpose or "").strip():
        overrides["core_purpose"] = core_purpose.strip()[:500]
    return overrides


@router.post("", response_model=ContractReviewDetailResponse)
async def post_contract_review(
    project_id: UUID,
    file: UploadFile = File(...),
    template_ids: str = Form(""),
    review_points: str = Form(""),
    core_purpose: str = Form(""),
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    ocr_provider: OcrProvider | None = Depends(get_ocr_provider),
) -> ContractReviewDetailResponse:
    await _require_project(session, user.tenant_id, project_id)
    filename = file.filename or "contract.txt"
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationAppError("仅支持 .docx / .pdf / .txt / .md 或图片（.png/.jpg/.jpeg/.webp）合同文件")
    content = await _read_upload(file)
    template_overrides = _parse_template_overrides(template_ids, review_points, core_purpose)
    payload = await _latest_constraint_payload(session, user.tenant_id, project_id)
    from app.modules.projects.commitments import list_commitments

    commitments = await list_commitments(session, user.tenant_id, project_id)
    try:
        review, clauses, findings = await create_review(
            session,
            tenant_id=user.tenant_id,
            project_id=project_id,
            user_id=user.id,
            filename=filename,
            content=content,
            constraint_payload=payload,
            commitments=commitments,
            template_overrides=template_overrides,
            ocr_provider=ocr_provider,
        )
    except AppError:
        raise
    except IntegrityError as exc:
        # 并发下预检查可能漏过，唯一约束兜底为 409 而不是 503。
        await session.rollback()
        raise ConflictError("该合同文件已审查过，请直接查看既有审查结果") from exc
    except (ScannedDocumentError, UnsupportedDocumentError, OcrConfigError) as exc:
        # 扫描件无文字层 / 不认识的格式，且没有 OCR 通道：可由用户或运维解决的
        # 明确条件，转 422 而不是吞成模糊 503。
        await session.rollback()
        raise ValidationAppError(
            "合同未检出文字层（扫描件或不支持的格式），且当前未配置 OCR 通道；"
            "请上传文字版或联系管理员启用 OCR"
        ) from exc
    except Exception as exc:
        await session.rollback()
        logging.getLogger("bidcopilot.contracts").exception(
            "contract parse failed for project %s", project_id
        )
        raise ServiceUnavailableError(f"合同解析失败：{type(exc).__name__}") from exc

    # 里程碑启发式抽取 + 租户自定义规则命中（与 re-review 共用的追加项）
    await _attach_review_extras(session, user, review, clauses)
    await session.commit()
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.review_created",
        resource_type="contract_review",
        resource_id=str(review.id),
        data={"filename": filename, "clauses": len(clauses), "findings": len(findings)},
    )
    await session.commit()
    # 里程碑与租户规则 finding 在 create_review 之后追加，响应前重查避免旧列表
    findings = await get_findings(session, review.id)
    return _detail(review, clauses, findings)


@router.get("", response_model=list[ContractReviewResponse])
async def get_contract_reviews(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ContractReviewResponse]:
    rows = await list_reviews(session, tenant_id=user.tenant_id, project_id=project_id)
    return [ContractReviewResponse.model_validate(row) for row in rows]


# 静态路径必须注册在 /{review_id} 之前：FastAPI 按注册顺序匹配，
# 否则 GET .../tenant-rules 会被当作 review_id 解析成 UUID 并返回 422。
class TenantRuleCreate(BaseModel):
    # rule_id 落 String(60)，前缀 tenant: 占 7 字符：name 上限 50，写入口拒绝而非 500。
    name: str = Field(min_length=1, max_length=50)
    pattern: str = Field(min_length=1, max_length=200)
    severity: str = "medium"
    message_template: str = Field(min_length=1, max_length=2000)


@router.get("/tenant-rules", response_model=list[dict[str, object]])
async def list_tenant_rules(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            _TenantRuleLookup(session, user.tenant_id)
            .scoped(select(TenantContractRule))
            .order_by(TenantContractRule.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "pattern": row.pattern,
            "severity": row.severity,
            "message_template": row.message_template,
            "enabled": row.enabled,
        }
        for row in rows
    ]


@router.post("/tenant-rules", response_model=dict[str, object])
async def create_tenant_rule(
    project_id: UUID,
    body: TenantRuleCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    import re as _re

    try:
        _re.compile(body.pattern)
    except _re.error as exc:
        raise ValidationAppError(f"非法正则：{exc}") from exc
    # 拒绝常见灾难性回溯形态（嵌套量词），并限制长度；避免上传链路被单条规则拖死。
    if _re.search(r"\([^)]*[+*][^)]*\)\s*[+*{]", body.pattern):
        raise ValidationAppError("正则存在嵌套量词（潜在 ReDoS），请简化")
    if body.severity not in ("high", "medium", "low"):
        raise ValidationAppError("severity 仅支持 high / medium / low")
    rule = TenantContractRule(
        tenant_id=user.tenant_id,
        name=body.name.strip(),
        pattern=body.pattern,
        severity=body.severity,
        message_template=body.message_template,
        created_by=user.id,
    )
    session.add(rule)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise ValidationAppError("同名规则已存在") from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.tenant_rule_created",
        resource_type="tenant_contract_rule",
        resource_id=str(rule.id),
        data={"name": rule.name, "severity": rule.severity},
    )
    await session.commit()
    return {
        "id": str(rule.id),
        "name": rule.name,
        "pattern": rule.pattern,
        "severity": rule.severity,
        "message_template": rule.message_template,
        "enabled": rule.enabled,
    }


@router.get("/{review_id}", response_model=ContractReviewDetailResponse)
async def get_contract_review(
    project_id: UUID,
    review_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewDetailResponse:
    review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if review is None or review.project_id != project_id:
        raise NotFoundError()
    clauses = await get_clauses(session, review_id)
    findings = await get_findings(session, review_id)
    return _detail(review, clauses, findings)


@router.patch("/findings/{finding_id}", response_model=ContractReviewDetailResponse)
async def patch_contract_finding(
    project_id: UUID,
    finding_id: UUID,
    body: FindingStatusUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewDetailResponse:
    if body.status not in {"confirmed", "dismissed"}:
        raise ValidationAppError("status 仅支持 confirmed / dismissed")
    finding = (
        await session.execute(
            _FindingLookup(session, user.tenant_id)
            .scoped(select(ContractFinding))
            .where(ContractFinding.id == finding_id)
        )
    ).scalar_one_or_none()
    review = await get_review(session, tenant_id=user.tenant_id, review_id=finding.review_id) if finding else None
    if finding is None or review is None or review.project_id != project_id:
        raise NotFoundError()
    from datetime import UTC, datetime

    finding.status = body.status
    finding.confirmed_by = user.id
    finding.confirmed_at = datetime.now(UTC)
    await session.commit()
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.finding_confirmed" if body.status == "confirmed" else "contract.finding_dismissed",
        resource_type="contract_finding",
        resource_id=str(finding.id),
        data={"rule_id": finding.rule_id, "kind": finding.kind},
    )
    await session.commit()
    clauses = await get_clauses(session, review.id)
    findings = await get_findings(session, review.id)
    return _detail(review, clauses, findings)


@router.get("/{review_id}/export")
async def export_contract_review(
    project_id: UUID,
    review_id: UUID,
    format: str = Query(default="xlsx", pattern="^(xlsx)$"),
    user: CurrentUser = Depends(require_permission("compliance:export")),
    session: AsyncSession = Depends(get_session),
) -> Any:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if review is None or review.project_id != project_id:
        raise NotFoundError()
    clauses = await get_clauses(session, review_id)
    findings = await get_findings(session, review_id)
    clause_title = {clause.id: f"第{clause.seq}条 {clause.title}" for clause in clauses}

    workbook = Workbook()
    sheet = active_sheet(workbook)
    sheet.title = "合同审查"
    headers = ["审查结论", "严重度", "消息", "法律依据", "关联条款", "状态", "证据摘录", "免责声明"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for finding in findings:
        sheet.append(
            excel_row(
                [
                    {"deviation": "实质性偏差", "risk": "风险", "missing": "缺口"}.get(
                        finding.kind, finding.kind
                    ),
                    {"high": "高", "medium": "中", "low": "低"}.get(
                        finding.severity, finding.severity
                    ),
                    finding.message,
                    finding.legal_basis or "",
                    clause_title.get(finding.clause_id, "") if finding.clause_id else "",
                    {"pending": "待确认", "confirmed": "已确认", "dismissed": "已驳回"}.get(
                        finding.status, finding.status
                    ),
                    str(finding.evidence_span.get("excerpt") or "")[:200],
                    DISCLAIMER,
                ]
            )
        )
    buffer = BytesIO()
    workbook.save(buffer)
    from fastapi import Response

    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="contract-review-{review_id}.xlsx"'},
    )


@router.post("/{review_id}/deep-review", response_model=ContractReviewDetailResponse)
async def post_contract_deep_review(
    project_id: UUID,
    review_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    llm_client: LLMClient = Depends(get_llm_client),
) -> ContractReviewDetailResponse:
    """LLM 深审：显式触发 = 授权计费（按次落成本台账与审计）。"""
    if not llm_client.configured():
        raise ValidationAppError("LLM 网关未配置，无法进行深度审查")
    review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if review is None or review.project_id != project_id:
        raise NotFoundError()
    clauses = await get_clauses(session, review_id)
    if not clauses:
        raise ValidationAppError("该审查记录没有条款，无法深度审查")
    try:
        created, batch_meta = await deep_review_with_llm(
            session, review=review, clauses=clauses, llm_client=llm_client
        )
    except ValidationAppError:
        raise
    except Exception as exc:
        await session.rollback()
        raise ServiceUnavailableError(f"LLM 深审失败：{type(exc).__name__}: {exc}") from exc
    # 批次可观测性进 summary_json（部分失败时前端据此显示黄条，不静默）。
    review.summary_json = {**(review.summary_json or {}), "llm_batches": batch_meta}
    # 先把 LLM findings 落库：计费/审计簿记失败不得把已计费的结论回滚掉。
    await session.commit()
    try:
        await record_gateway_call(
            session,
            tenant_id=user.tenant_id,
            pcode="llm",
            operation="contract.deep_review",
            status="ok",
            order_no=None,
            charged=True,
            rows=len(created),
            query_hash_value=query_hash({"review_id": str(review_id)}),
            resource_type="contract_review",
            resource_id=str(review_id),
            created_by=user.id,
        )
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="contract.deep_review",
            resource_type="contract_review",
            resource_id=str(review_id),
            data={"findings": len(created), "batches": batch_meta},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        import logging

        logging.getLogger("bidcopilot.contracts").exception(
            "contract deep review bookkeeping failed for review %s", review_id
        )
    review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    clauses = await get_clauses(session, review_id)
    findings = await get_findings(session, review_id)
    return _detail(review, clauses, findings)


@router.post("/{review_id}/re-review", response_model=ContractReviewDetailResponse)
async def post_contract_re_review(
    project_id: UUID,
    review_id: UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    ocr_provider: OcrProvider | None = Depends(get_ocr_provider),
) -> ContractReviewDetailResponse:
    """修订对照：上传新版本合同跑全流水线，与原审查做 findings 差集。

    diff 结果持久化在新审查的 summary_json.revision_diff（added/resolved/
    carried），前端据此呈现三组列表；确认流照常作用于新 findings。
    """
    await _require_project(session, user.tenant_id, project_id)
    old_review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if old_review is None or old_review.project_id != project_id:
        raise NotFoundError()
    filename = file.filename or "contract.txt"
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationAppError("仅支持 .docx / .pdf / .txt / .md 或图片（.png/.jpg/.jpeg/.webp）合同文件")
    content = await _read_upload(file)
    if hashlib.sha256(content).hexdigest() == old_review.file_hash:
        raise ValidationAppError("新文件与原审查文件相同，没有变更可对照")
    payload = await _latest_constraint_payload(session, user.tenant_id, project_id)
    from app.modules.projects.commitments import list_commitments

    commitments = await list_commitments(session, user.tenant_id, project_id)
    try:
        review, clauses, findings = await create_review(
            session,
            tenant_id=user.tenant_id,
            project_id=project_id,
            user_id=user.id,
            filename=filename,
            content=content,
            constraint_payload=payload,
            commitments=commitments,
            ocr_provider=ocr_provider,
        )
    except AppError:
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("该合同文件已审查过，请直接查看既有审查结果") from exc
    except (ScannedDocumentError, UnsupportedDocumentError, OcrConfigError) as exc:
        await session.rollback()
        raise ValidationAppError(
            "合同未检出文字层（扫描件或不支持的格式），且当前未配置 OCR 通道；"
            "请上传文字版或联系管理员启用 OCR"
        ) from exc
    except Exception as exc:
        await session.rollback()
        logging.getLogger("bidcopilot.contracts").exception(
            "contract re-review parse failed for project %s (of %s)", project_id, review_id
        )
        raise ServiceUnavailableError(f"合同解析失败：{type(exc).__name__}") from exc
    review.replaces_review_id = review_id
    await _attach_review_extras(session, user, review, clauses)
    old_findings = await get_findings(session, review_id)
    new_findings = await get_findings(session, review.id)
    review.summary_json = {
        **(review.summary_json or {}),
        "revision_diff": diff_re_review(old_findings, new_findings),
        "re_review_of": str(review_id),
    }
    await session.commit()
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.re_review",
        resource_type="contract_review",
        resource_id=str(review.id),
        data={"of": str(review_id), "filename": filename},
    )
    await session.commit()
    findings = await get_findings(session, review.id)
    return _detail(review, clauses, findings)


@router.get("/{review_id}/milestones")
async def get_contract_milestones(
    project_id: UUID,
    review_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if review is None or review.project_id != project_id:
        raise NotFoundError()
    rows = (
        await session.execute(
            _MilestoneLookup(session, user.tenant_id)
            .scoped(select(ContractMilestone))
            .where(ContractMilestone.review_id == review_id)
            .order_by(ContractMilestone.due_at.nullslast())
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "title": row.title,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "amount": str(row.amount) if row.amount is not None else None,
            "status": row.status,
            "reminded_at": row.reminded_at.isoformat() if row.reminded_at else None,
        }
        for row in rows
    ]


@router.post("/{review_id}/supplements", response_model=ContractReviewDetailResponse)
async def post_contract_supplement(
    project_id: UUID,
    review_id: UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractReviewDetailResponse:
    """补充协议上传：以原审查条款为基线 diff，变更点落 findings。"""
    filename = file.filename or "supplement.txt"
    content = await _read_upload(file)
    base_review = await get_review(session, tenant_id=user.tenant_id, review_id=review_id)
    if base_review is None or base_review.project_id != project_id:
        raise NotFoundError()
    base_clauses = await get_clauses(session, review_id)
    base_data = [
        {"seq": c.seq, "title": c.title, "text": c.text, "category": c.category, "page": c.page}
        for c in base_clauses
    ]
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationAppError("仅支持 .docx / .pdf / .txt / .md 合同文件")
    from app.modules.tender.parser import parse_document

    try:
        parsed = parse_document(content, filename)
    except Exception as exc:
        raise ValidationAppError(f"补充协议解析失败：{type(exc).__name__}") from exc
    supplement_clauses = split_clauses(parsed)
    diff_findings = diff_supplement(base_data, supplement_clauses)

    import hashlib as _hashlib

    supplement = ContractReview(
        tenant_id=user.tenant_id,
        project_id=project_id,
        filename=filename,
        size_bytes=len(content),
        file_hash=_hashlib.sha256(content).hexdigest(),
        status="reviewed",
        summary_json={
            "clauses": len(supplement_clauses),
            "findings": {
                "high": sum(1 for f in diff_findings if f["severity"] == "high"),
                "medium": sum(1 for f in diff_findings if f["severity"] == "medium"),
                "low": sum(1 for f in diff_findings if f["severity"] == "low"),
            },
            "checked_rules": 0,
            "skipped_consistency": True,
            "supplement_of": str(review_id),
        },
        created_by=user.id,
    )
    session.add(supplement)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 与主合同同文件或重复上传补充协议：唯一约束冲突必须是 409，不是 500。
        await session.rollback()
        raise ConflictError("该文件已上传过（含主合同），不能重复作为补充协议") from exc
    for data in diff_findings:
        session.add(
            ContractFinding(
                tenant_id=user.tenant_id,
                review_id=supplement.id,
                clause_id=None,
                kind=data["kind"],
                rule_id=data["rule_id"],
                severity=data["severity"],
                message=data["message"],
                legal_basis=data.get("legal_basis"),
                evidence_span=data.get("evidence_span") or {},
                source="rule",
            )
        )
    await session.commit()
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="contract.supplement_reviewed",
        resource_type="contract_review",
        resource_id=str(supplement.id),
        data={"base_review": str(review_id), "findings": len(diff_findings)},
    )
    await session.commit()
    clauses = await get_clauses(session, supplement.id)
    findings = await get_findings(session, supplement.id)
    return _detail(supplement, clauses, findings)
