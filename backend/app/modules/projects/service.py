import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationAppError,
)
from app.core.time import business_today, local_date
from app.db.locks import advisory_lock
from app.db.session import SessionLocal, get_session  # noqa: F401  (get_session re-exported for dependency wiring)
from app.modules.assets.access import visibility_filter
from app.modules.assets.field_policies import apply_field_policies
from app.modules.assets.models import Credit, Performance, Personnel, Qualification
from app.modules.assets.repository import AssetRepository, CompanyRepository
from app.modules.costs.service import record_gateway_call
from app.modules.gateway.snapshots import build_snapshot_store
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications import webhooks
from app.modules.opportunities.repository import OpportunityRepository
from app.modules.quota.policy import enforce_project_limit
from app.modules.projects.matching import match_scoring_item, qualification_matches
from app.modules.projects.models import (
    BidProject,
    ComplianceResponse,
    ParseJob,
    ResponseMatrix,
    ScoringItem,
    TenderConstraint,
    TenderDocument,
)
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    ConstraintResponse,
    DecisionResponse,
    DocumentResponse,
    MatrixResponse,
    ProjectCreate,
    ProjectUpdate,
    QualificationItemResponse,
)
from app.modules.tender.fallback import apply_fallback
from app.modules.tender.ocr import (
    IMAGE_SUFFIXES,
    MINERU_SUFFIXES,
    OcrConfigError,
    OcrError,
    OcrProvider,
    ocr_document,
)
from app.modules.tender.parser import (
    ScannedDocumentError,
    UnsupportedDocumentError,
    build_constraint_set,
    find_publish_date,
    parse_document,
    parsed_bid_deadline,
    resolve_relative_timeline,
)

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
ASYNC_PARSE_THRESHOLD_BYTES = 10 * 1024 * 1024
DOCUMENT_KINDS = ("tender", "clarification", "amendment", "final")
# 上传白名单 = 本地解析器可直解的类型 + MinerU 支持的全集（后者解析失败时
# 回退 OCR 通道，见 _execute_parse）。
SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt", ".md"} | IMAGE_SUFFIXES | MINERU_SUFFIXES


def _asset_text(asset: Any, name: str) -> dict[str, Any]:
    return {"id": str(asset.id), "name": name}


def resolve_project_name(explicit: str | None, opportunity: Any | None) -> str:
    """项目名称取值优先级：显式传入 > 商机「项目名称」> 公告标题。

    公告标题常带公告类型后缀（如「…项目更正公告」「…竞争性磋商公告」），直接拿来
    当项目名会一路带到导出标书、交付摘要与合同台账；`project_name` 才是标的本身
    的名称。此口径与同函数里 `code` 取 `project_code` 保持一致。
    """
    if explicit and explicit.strip():
        return explicit.strip()
    if opportunity is None:
        return ""
    # 先 strip 再判断：project_name 可能是纯空白，不能被 `or` 当成有效值。
    project_name = (opportunity.project_name or "").strip()
    if project_name:
        return project_name
    return (opportunity.title or "").strip()


async def create_project(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    body: ProjectCreate,
) -> BidProject:
    opportunity = None
    if body.opportunity_id is not None:
        opportunity = await OpportunityRepository(session, tenant_id).get_or_404(body.opportunity_id)
    company = None
    if body.company_id is not None:
        company = await CompanyRepository(session, tenant_id).get_or_404(body.company_id)
    name = resolve_project_name(body.name, opportunity)
    if not name:
        raise ValidationAppError("项目名称不能为空")
    from app.modules.quota.policy import project_creation_guard

    async with project_creation_guard(tenant_id):
        await enforce_project_limit(session, tenant_id)
        project = BidProject(
            tenant_id=tenant_id,
            opportunity_id=opportunity.id if opportunity else None,
            company_id=company.id if company else None,
            name=name,
            code=body.code or (opportunity.project_code if opportunity else None),
            buyer=opportunity.buyer if opportunity else None,
            status="active",
            owner_id=user_id,
            bid_deadline=body.bid_deadline or (opportunity.bid_deadline if opportunity else None),
            internal_deadline=getattr(body, "internal_deadline", None),
        )
        session.add(project)
        await session.commit()
    await session.refresh(project)
    return project


# 项目状态机：只允许这些流转。
# won/lost 是终态，不允许再改回 active——中标结果不能因为点错就抹掉，
# 真要重开应该新建项目，这样历史才可信。
PROJECT_TRANSITIONS: dict[str, set[str]] = {
    "active": {"won", "lost", "cancelled"},
    # 已中标允许更正为未中标（复盘录入错误的人工纠正），其余终态不可回写。
    "won": {"lost"},
    "lost": {"active", "won"},
    "cancelled": set(),
}
PROJECT_STATUS_LABELS = {"active": "在投", "won": "已中标", "lost": "未中标", "cancelled": "已取消"}


async def change_project_status(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    new_status: str,
    reason: str | None = None,
) -> BidProject:
    """项目状态流转。非法流转一律拒绝，不做「尽力而为」。"""
    project = await get_project(session, tenant_id, project_id)
    current = project.status
    if new_status == current:
        return project
    allowed = PROJECT_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValidationAppError(
            f"不允许从「{PROJECT_STATUS_LABELS.get(current, current)}」变更为「{PROJECT_STATUS_LABELS.get(new_status, new_status)}」"
        )
    if new_status == "lost" and not (reason or "").strip():
        # 未中标必须写原因：复盘和「同类项目要不要再投」都靠这个
        raise ValidationAppError("标记未中标时必须填写原因")
    project.status = new_status
    await session.commit()
    await session.refresh(project)
    # 项目状态变更事件：落库成功后登记后台投递，失败不影响主流程。
    webhooks.notify_project_status_changed(
        tenant_id,
        project_id=project.id,
        from_status=current,
        to_status=new_status,
        reason=reason,
    )
    return project


async def update_project(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    body: ProjectUpdate,
) -> BidProject:
    project = await get_project(session, tenant_id, project_id)
    if body.company_id is not None:
        company = await CompanyRepository(session, tenant_id).get_or_404(body.company_id)
        project.company_id = company.id
    if body.name is not None:
        project.name = body.name.strip() or project.name
    if body.code is not None:
        project.code = body.code
    if body.bid_deadline is not None:
        project.bid_deadline = body.bid_deadline
    if body.internal_deadline is not None:
        project.internal_deadline = body.internal_deadline
    if body.package_code is not None:
        project.package_code = body.package_code.strip() or None
    await session.commit()
    await session.refresh(project)
    return project


async def list_projects(session: AsyncSession, tenant_id: UUID) -> list[BidProject]:
    repo = ProjectRepository(session, tenant_id)
    statement = repo.scoped(select(BidProject))
    return list(
        (
            await session.execute(statement.order_by(BidProject.created_at.desc()))
        ).scalars().all()
    )


logger = logging.getLogger("bidcopilot.projects")


async def get_project(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> BidProject:
    return await ProjectRepository(session, tenant_id).get_or_404(project_id)


async def get_latest_constraint(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> TenderConstraint | None:
    return (
        await session.execute(
            select(TenderConstraint)
            .where(
                TenderConstraint.tenant_id == tenant_id,
                TenderConstraint.project_id == project_id,
            )
            .order_by(TenderConstraint.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_documents(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> list[TenderDocument]:
    return list(
        (
            await session.execute(
                select(TenderDocument)
                .where(
                    TenderDocument.tenant_id == tenant_id,
                    TenderDocument.project_id == project_id,
                )
                .order_by(TenderDocument.created_at.desc())
            )
        ).scalars().all()
    )


MERGE_KEYS = (
    "reject_clauses",
    "scoring_items",
    "qualification_requirements",
    "substantive_requirements",
    "submission_checklist",
)

# 补遗（补遗书/更正公告）通常只写变更项。这些维度若补遗里没解析出内容，
# 视为「补遗未提及」并沿用上一版；只有补遗真的写了该维度才替换。
# 否则最新约束集会丢评分项/资格/否决条款，评分矩阵、AI 评标、逐条响应会整条归零。
AMENDMENT_INHERIT_LIST_KEYS = (
    "reject_clauses",
    "scoring_items",
    "qualification_requirements",
    "substantive_requirements",
    "submission_checklist",
    "format_rules",
    "technical_parameters",
    "front_appendix",
    "star_clauses",
    "clarifications",
    "timeline",
    "bid_risks",
)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str | list | tuple | dict | set):
        return len(value) == 0
    return False


def _merge_amendment(
    payload: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """补遗按维度覆盖：补遗写到的维度以后者为准，没写到的沿用上一版全量约束。

    与澄清的差别：澄清只补空缺，补遗可以替换它明确写到的维度（例如改预算、
    改截止时间、重述评分标准），但不得因为「没提」而清空原招标文件的要求。
    """
    if not previous:
        payload["parse_meta"]["merged_clarification"] = False
        payload["parse_meta"]["amendment_inherited_dimensions"] = []
        return payload

    merged = dict(payload)
    inherited: list[str] = []
    for key in AMENDMENT_INHERIT_LIST_KEYS:
        if _is_blank(payload.get(key)) and not _is_blank(previous.get(key)):
            merged[key] = previous.get(key)
            inherited.append(key)

    previous_budget = previous.get("budget") or {}
    if _is_blank((payload.get("budget") or {}).get("amount")) and not _is_blank(
        previous_budget.get("amount")
    ):
        merged["budget"] = previous_budget
        inherited.append("budget")

    previous_deposit = previous.get("deposit") or {}
    if _is_blank((payload.get("deposit") or {}).get("amount")) and not _is_blank(
        previous_deposit.get("amount")
    ):
        merged["deposit"] = previous_deposit
        inherited.append("deposit")

    if payload.get("bid_validity_days") is None and previous.get("bid_validity_days") is not None:
        merged["bid_validity_days"] = previous.get("bid_validity_days")
        inherited.append("bid_validity_days")

    packages = payload.get("packages") or {}
    previous_packages = previous.get("packages") or {}
    if (
        _is_blank(packages.get("declared_count"))
        and _is_blank(packages.get("items"))
        and (
            not _is_blank(previous_packages.get("declared_count"))
            or not _is_blank(previous_packages.get("items"))
        )
    ):
        merged["packages"] = previous_packages
        inherited.append("packages")

    dark_bid = payload.get("dark_bid") or {}
    previous_dark = previous.get("dark_bid") or {}
    if (
        not dark_bid.get("required")
        and _is_blank(dark_bid.get("rules"))
        and (previous_dark.get("required") or not _is_blank(previous_dark.get("rules")))
    ):
        # 补遗未提暗标要求时保留原要求：合规宁可严格，不因缺省而放宽
        merged["dark_bid"] = previous_dark
        inherited.append("dark_bid")

    merged["parse_meta"] = dict(payload.get("parse_meta") or {})
    merged["parse_meta"]["merged_clarification"] = False
    merged["parse_meta"]["amendment_inherited_dimensions"] = sorted(set(inherited))
    return merged


def _merge_clarification(payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """澄清/答疑文件只补充改动项，不得覆盖原招标文件已解析出的要求。"""
    if not previous or not payload.get("clarifications"):
        return payload
    # 出现澄清即合并：新文件解析出的内容优先，只补齐空缺，避免澄清覆盖原招标要求

    merged = dict(payload)
    # 澄清文件不重新定义评分表与资格条件：这些以原招标文件为准，避免误解析顶掉正确结果
    for key in MERGE_KEYS:
        merged[key] = previous.get(key) or merged.get(key) or []
    existing_texts = {str(item.get("text")) for item in payload.get("clarifications") or []}
    merged["clarifications"] = list(payload.get("clarifications") or []) + [
        item
        for item in previous.get("clarifications") or []
        if str(item.get("text")) not in existing_texts
    ]
    if not (merged.get("budget") or {}).get("amount"):
        merged["budget"] = previous.get("budget") or {"amount": None, "currency": "CNY"}
    if not merged.get("timeline"):
        merged["timeline"] = previous.get("timeline") or []
    if not (merged.get("dark_bid") or {}).get("required"):
        merged["dark_bid"] = previous.get("dark_bid") or {"required": False, "rules": []}
    merged["parse_meta"] = {**previous.get("parse_meta", {}), **payload.get("parse_meta", {})}
    merged["parse_meta"]["merged_clarification"] = True
    return merged


async def _execute_parse(
    session: AsyncSession,
    *,
    project: Any,
    document: TenderDocument,
    content: bytes,
    filename: str,
    suffix: str,
    vision_client: OcrProvider | None,
    user_id: UUID | None,
) -> TenderConstraint | None:
    """解析→约束集→评分项落库（同步上传与异步 ParseJob 共用）。"""
    try:
        ocr_used = False
        ocr_meta_provider: str | None = None
        ocr_structure_used = False
        if suffix in IMAGE_SUFFIXES:
            if vision_client is None:
                raise OcrConfigError("OCR 服务未配置")
            parsed, ocr_result = await ocr_document(
                vision_client,
                content,
                filename,
                max_pages=get_settings().MINIMAX_OCR_MAX_PAGES,
            )
            ocr_used = True
            ocr_meta_provider = ocr_result.provider
            ocr_structure_used = ocr_result.structure_used
        else:
            try:
                parsed = parse_document(content, filename)
            except (ScannedDocumentError, UnsupportedDocumentError):
                # 扫描版 PDF 无文字层，或本地解析器不认识的类型（pptx/xlsx/epub/
                # ofd 等 MinerU 全集）：有 OCR 通道就交给本地 OCR 解析，否则原样透出。
                if vision_client is None:
                    raise
                parsed, ocr_result = await ocr_document(
                    vision_client,
                    content,
                    filename,
                    max_pages=get_settings().MINIMAX_OCR_MAX_PAGES,
                )
                ocr_used = True
                ocr_meta_provider = ocr_result.provider
                ocr_structure_used = ocr_result.structure_used
        payload = build_constraint_set(
            parsed,
            {
                "name": project.name,
                "code": project.code or "",
                "buyer": project.buyer or "",
                "bid_deadline": project.bid_deadline.isoformat() if project.bid_deadline else "",
            },
        )
        previous = await get_latest_constraint(session, project.tenant_id, project.id)
        if document.document_kind == "amendment":
            # 补遗漏掉的维度沿用上一版：避免「只改预算」把评分项/资格/否决条款清空，
            # 进而让评分矩阵、AI 评标、逐条响应、合规预检全部归零。
            payload = _merge_amendment(payload, previous.payload if previous else None)
        else:
            # 澄清仍只补不改；tender 保留旧的自动识别行为，兼容既有上传
            payload = _merge_clarification(payload, previous.payload if previous else None)
        payload["parse_meta"]["document_kind"] = document.document_kind
        # 「自公告发布之日起 5 个工作日」这类相对表述，用公告发布日换算成绝对日期。
        # 没有发布日时保留 needs_confirmation，并在 parse_meta 里给出提示——
        # 静默丢弃会让项目没有截止时间，提醒/就绪校验/逾期判断全部失效。
        timeline, timeline_notes = resolve_relative_timeline(
            payload["timeline"], find_publish_date(parsed.blocks)
        )
        payload["timeline"] = timeline
        if timeline_notes:
            payload["parse_meta"]["timeline_notes"] = timeline_notes
        if project.bid_deadline is None:
            inferred = parsed_bid_deadline(payload["timeline"])
            if inferred is not None:
                project.bid_deadline = inferred
        fallback_report = None
        settings = get_settings()
        if settings.TENDER_UIE_FALLBACK_ENABLED or settings.TENDER_LLM_FALLBACK_ENABLED:
            from app.modules.generation.llm_client import build_llm_client_from_settings

            llm_client = (
                build_llm_client_from_settings()
                if settings.TENDER_LLM_FALLBACK_ENABLED
                else None
            )
            fallback_report = await apply_fallback(
                payload,
                parsed,
                uie_enabled=settings.TENDER_UIE_FALLBACK_ENABLED,
                llm_enabled=settings.TENDER_LLM_FALLBACK_ENABLED,
                llm_client=llm_client,
            )
            meta = payload["parse_meta"]
            meta["uie_used"] = fallback_report.uie_used
            meta["llm_used"] = fallback_report.llm_used
            meta["fallback_dimensions"] = fallback_report.dimensions or []
            if fallback_report.uie_items:
                meta["uie_items"] = fallback_report.uie_items
            if fallback_report.llm_items:
                meta["llm_items"] = fallback_report.llm_items
            warnings_meta = meta.setdefault("warnings", [])
            if fallback_report.uie_error:
                warnings_meta.append(fallback_report.uie_error[:300])
            if fallback_report.llm_error:
                warnings_meta.append(fallback_report.llm_error[:300])
            if fallback_report.llm_used:
                # 计费透明：LLM 兜底按调用落台账（与网关调用同口径）。
                try:
                    await record_gateway_call(
                        session,
                        tenant_id=project.tenant_id,
                        pcode="llm",
                        operation="tender.llm_fallback",
                        status="ok",
                        order_no=None,
                        charged=True,
                        rows=fallback_report.llm_items,
                        resource_type="project",
                        resource_id=str(project.id),
                        created_by=user_id,
                    )
                except Exception:
                    await session.rollback()
                    logger.exception("llm fallback bookkeeping failed")

        if ocr_used and ocr_meta_provider is not None:
            payload["parse_meta"]["ocr_used"] = True
            payload["parse_meta"]["ocr_provider"] = ocr_meta_provider
            payload["parse_meta"]["ocr_structure_used"] = ocr_structure_used
            payload["parse_meta"]["ocr_pages"] = parsed.page_count
    except ScannedDocumentError as exc:
        document.parse_status = "failed"
        document.parse_error = str(exc)
        await session.commit()
        return None
    except OcrError as exc:
        document.parse_status = "failed"
        document.parse_error = f"ocr_failed: {exc}"
        await session.commit()
        return None
    except UnsupportedDocumentError as exc:
        document.parse_status = "failed"
        document.parse_error = str(exc)
        await session.commit()
        raise
    except Exception as exc:
        document.parse_status = "failed"
        document.parse_error = f"{exc.__class__.__name__}: {exc}"
        await session.commit()
        return None

    document.parse_status = "parsed"
    document.parsed_at = datetime.now(UTC)
    document.ocr_used = ocr_used
    max_version = (
        await session.execute(
            select(func.max(TenderConstraint.version)).where(
                TenderConstraint.project_id == project.id
            )
        )
    ).scalar_one()
    version = int(max_version or 0) + 1
    constraint = TenderConstraint(
        tenant_id=project.tenant_id,
        project_id=project.id,
        document_id=document.id,
        version=version,
        payload=payload,
        parse_meta=payload.get("parse_meta", {}),
    )
    session.add(constraint)
    await session.flush()
    for item in payload.get("scoring_items", []):
        score = item.get("score")
        session.add(
            ScoringItem(
                tenant_id=project.tenant_id,
                project_id=project.id,
                constraint_id=constraint.id,
                category=item.get("category") or "其他",
                item=item.get("item") or "",
                score=Decimal(str(score)) if score is not None else None,
                criteria=item.get("criteria"),
                source_span=item.get("source_span") or {},
                confidence=item.get("confidence") or "medium",
            )
        )
    await session.commit()
    await session.refresh(document)
    await session.refresh(constraint)
    return constraint


async def upload_document(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    filename: str,
    content_type: str | None,
    content: bytes,
    vision_client: OcrProvider | None = None,
    user_id: UUID | None = None,
    document_kind: str = "tender",
) -> tuple[TenderDocument, TenderConstraint | None, ParseJob | None]:
    project = await get_project(session, tenant_id, project_id)
    if document_kind not in DOCUMENT_KINDS:
        raise ValidationAppError("无效的文档类型")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValidationAppError("文件大小超过 20MB 上限")
    filename = Path(filename).name.strip()[:200]
    if not filename:
        raise ValidationAppError("文件名不能为空")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValidationAppError("仅支持 .docx / .pdf / .txt / .md 与图片文件")

    file_hash = hashlib.sha256(content).hexdigest()
    try:
        store = build_snapshot_store(get_settings())
        object_ref = await store.put_bytes(
            f"tender/{tenant_id}/{project_id}/{file_hash[:12]}-{filename}",
            content,
            content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise ServiceUnavailableError("文档存储不可用") from exc

    # 7.2 Work 身份：同项目新上传为下一版本，supersedes 指向当前最新版
    previous_doc = (
        await session.execute(
            select(TenderDocument)
            .where(
                TenderDocument.tenant_id == tenant_id,
                TenderDocument.project_id == project_id,
            )
            .order_by(TenderDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_version = (previous_doc.version + 1) if previous_doc else 1

    # 7.3 长文档异步：>10MB 走 ParseJob 排队，不在请求内同步解析
    async_parse = len(content) > ASYNC_PARSE_THRESHOLD_BYTES
    document = TenderDocument(
        tenant_id=tenant_id,
        project_id=project_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        file_hash=file_hash,
        object_ref=object_ref,
        parse_status="queued" if async_parse else "parsing",
        version=next_version,
        supersedes_id=previous_doc.id if previous_doc else None,
        document_kind=document_kind,
    )
    session.add(document)
    await session.flush()
    parse_job: ParseJob | None = None
    if document_kind == "final":
        # 最终标书不是招标文件：不进解析流水线，只做装配体检
        from app.modules.tender.assembly import analyze_final_document

        document.assembly_meta = analyze_final_document(
            content, filename, project_name=project.name
        )
        document.parse_status = "analyzed"
        document.parsed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(document)
        return document, None, None
    if async_parse:
        parse_job = ParseJob(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document.id,
            status="queued",
            created_by=user_id,
        )
        session.add(parse_job)
        await session.commit()
        await session.refresh(document)
        await session.refresh(parse_job)
        return document, None, parse_job

    constraint = await _execute_parse(
        session,
        project=project,
        document=document,
        content=content,
        filename=filename,
        suffix=suffix,
        vision_client=vision_client,
        user_id=user_id,
    )
    await session.commit()
    await session.refresh(document)
    if constraint is not None:
        await session.refresh(constraint)
    return document, constraint, None


async def _load_assets_base(
    session: AsyncSession,
    tenant_id: UUID,
    company_id: UUID | None,
    user: CurrentUser | None = None,
) -> dict[str, list[dict[str, Any]]]:
    empty: dict[str, list[dict[str, Any]]] = {
        "qualifications": [],
        "personnel": [],
        "performances": [],
        "credits": [],
    }
    if company_id is None:
        return empty
    await CompanyRepository(session, tenant_id).get_or_404(company_id)

    async def rows(model: Any, name_field: str) -> list[Any]:
        repo = AssetRepository(session, tenant_id, model)
        statement = repo.scoped(select(model).where(model.company_id == company_id))
        if user is not None:
            statement = statement.where(visibility_filter(user, model))
        return list((await session.execute(statement)).scalars().all())

    qualifications = await rows(Qualification, "name")
    personnel = await rows(Personnel, "name")
    performances = await rows(Performance, "project_name")
    credits = await rows(Credit, "check_item")
    return {
        "qualifications": [
            {
                "id": str(item.id),
                "name": item.name,
                "level": item.level,
                "valid_to": item.valid_to.isoformat() if item.valid_to else None,
            }
            for item in qualifications
        ],
        "personnel": [
            {
                "id": str(item.id),
                "name": item.name,
                "cert_type": item.cert_type,
                "level": item.level,
                "valid_to": item.valid_to.isoformat() if item.valid_to else None,
            }
            for item in personnel
        ],
        "performances": [
            {
                "id": str(item.id),
                "name": item.project_name,
                "amount": str(item.amount) if item.amount is not None else None,
                "complete_date": item.complete_date.isoformat() if item.complete_date else None,
                "buyer": item.buyer,
            }
            for item in performances
        ],
        "credits": [
            {
                "id": str(item.id),
                "name": item.check_item,
                "status": item.status,
                "checked_at": item.checked_at.isoformat() if item.checked_at else None,
            }
            for item in credits
        ],
    }


async def load_assets_for_evaluation(
    session: AsyncSession,
    tenant_id: UUID,
    company_id: UUID | None,
    user: CurrentUser | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """计算用：只做可见性过滤，不做字段脱敏，保证资格/评分/合规结果与用户密级无关。"""
    return await _load_assets_base(session, tenant_id, company_id, user=user)


async def load_assets_for_user(
    session: AsyncSession,
    tenant_id: UUID,
    company_id: UUID | None,
    user: CurrentUser | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """展示/生成/导出用：可见性过滤后再按字段策略脱敏。"""
    assets = await _load_assets_base(session, tenant_id, company_id, user=user)
    if user is None:
        return assets
    for kind, items in assets.items():
        await apply_field_policies(session, user, kind, items)
    return assets


async def build_matrix(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser | None = None,
) -> list[MatrixResponse]:
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        raise NotFoundError("约束集不存在")
    items = (
        await session.execute(
            select(ScoringItem)
            .where(
                ScoringItem.tenant_id == tenant_id,
                ScoringItem.project_id == project_id,
                ScoringItem.constraint_id == constraint.id,
            )
            .order_by(ScoringItem.created_at)
        )
    ).scalars().all()
    assets = await load_assets_for_evaluation(session, tenant_id, project.company_id, user=user)
    results: list[MatrixResponse] = []
    for index, item in enumerate(items, start=1):
        matched = match_scoring_item(item.item, item.criteria, assets)
        values = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "scoring_item_id": item.id,
            "response_strategy": matched["response_strategy"],
            "evidence_required": matched["evidence_required"],
            "matched_assets": matched["matched_assets"],
            "missing_evidence": matched["missing_evidence"],
            "section_mapping": f"第{index}章",
            "coverage_status": matched["coverage_status"],
            "risk_note": matched["risk_note"],
        }
        await session.execute(
            insert(ResponseMatrix)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["scoring_item_id"],
                set_={key: value for key, value in values.items() if key not in {"tenant_id", "project_id", "scoring_item_id"}},
            )
        )
        results.append(
            MatrixResponse(
                scoring_item_id=item.id,
                item=item.item,
                category=item.category,
                score=item.score,
                coverage_status=matched["coverage_status"],
                evidence_required=matched["evidence_required"],
                matched_assets=matched["matched_assets"],
                missing_evidence=matched["missing_evidence"],
                response_strategy=matched["response_strategy"],
                risk_note=matched["risk_note"],
                section_mapping=f"第{index}章",
            )
        )
    await session.commit()
    return results


async def get_matrix(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> list[dict[str, Any]]:
    await get_project(session, tenant_id, project_id)
    rows = (
        await session.execute(
            select(ResponseMatrix, ScoringItem)
            .join(ScoringItem, ScoringItem.id == ResponseMatrix.scoring_item_id)
            .where(
                ResponseMatrix.tenant_id == tenant_id,
                ResponseMatrix.project_id == project_id,
            )
            .order_by(ResponseMatrix.created_at)
        )
    ).all()
    return [
        {
            "scoring_item_id": str(matrix.scoring_item_id),
            "item": item.item,
            "criteria": item.criteria,
            "category": item.category,
            "score": item.score,
            "coverage_status": matrix.coverage_status,
            "evidence_required": matrix.evidence_required,
            "matched_assets": matrix.matched_assets,
            "missing_evidence": matrix.missing_evidence,
            "response_strategy": matrix.response_strategy,
            "risk_note": matrix.risk_note,
            "section_mapping": matrix.section_mapping,
        }
        for matrix, item in rows
    ]


async def qualification_check(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    user: CurrentUser | None = None,
) -> tuple[list[QualificationItemResponse], dict[str, int]]:
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    requirements = (constraint.payload or {}).get("qualification_requirements", []) if constraint else []
    assets = await load_assets_for_evaluation(session, tenant_id, project.company_id, user=user)
    deadline = local_date(project.bid_deadline) if project.bid_deadline else business_today()
    results = qualification_matches(requirements, assets, deadline)
    items = [QualificationItemResponse(**result) for result in results]
    summary = {
        "PASS": sum(1 for item in items if item.status == "PASS"),
        "FAIL": sum(1 for item in items if item.status == "FAIL"),
        "UNKNOWN": sum(1 for item in items if item.status == "UNKNOWN"),
    }
    return items, summary


async def make_decision(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    user: CurrentUser | None = None,
) -> tuple[DecisionResponse, list[QualificationItemResponse], dict[str, int]]:
    """旧 Bid-No-Bid 接口：委托统一决策引擎的 legacy 模式，不再各写一套算法。"""
    from app.modules.projects.decision_card import compute_legacy_decision

    if user is None:
        raise ValidationAppError("生成决策需要用户上下文")
    payload, qualifications, summary = await compute_legacy_decision(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        user=user,
    )
    return DecisionResponse(**payload), qualifications, summary


def to_document_response(document: TenderDocument) -> DocumentResponse:
    return DocumentResponse.model_validate(document)


def to_constraint_response(constraint: TenderConstraint) -> ConstraintResponse:
    return ConstraintResponse.model_validate(constraint)


RESPONSE_STATUSES = ("pending", "responded", "deviated", "not_responded")


def _requirement_key(kind: str, text: str) -> str:
    normalized = re.sub(r"\s+", "", text)
    return hashlib.sha1(f"{kind}:{normalized}".encode()).hexdigest()


def _response_items(payload: dict[str, Any]) -> list[tuple[str, str, str, dict]]:
    items: list[tuple[str, str, str, dict]] = []
    seen: set[str] = set()
    for row in payload.get("qualification_requirements") or []:
        text = str(row.get("text") or "").strip()
        if text:
            key = re.sub(r"\s+", "", text)
            seen.add(key)
            items.append(("qualification", str(row.get("category") or "其他"), text, row.get("source_span") or {}))
    qualification_keys = list(seen)
    for row in payload.get("substantive_requirements") or []:
        text = str(row.get("text") or "").strip()
        key = re.sub(r"\s+", "", text)
        if not text or key in seen:
            continue
        # 资格要求已覆盖同一句话时不再重复计入实质性要求
        if any(key in existing_key or existing_key in key for existing_key in qualification_keys):
            continue
        seen.add(key)
        items.append(("substantive", str(row.get("category") or "其他"), text, row.get("source_span") or {}))
    for row in payload.get("clarifications") or []:
        text = str(row.get("text") or "").strip()
        key = re.sub(r"\s+", "", text)
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(("clarification", "澄清", text, row.get("source_span") or {}))
    for row in payload.get("scoring_items") or []:
        text = str(row.get("item") or "").strip()
        if not text or re.sub(r"\s+", "", text) in seen:
            continue
        seen.add(re.sub(r"\s+", "", text))
        items.append(("scoring", str(row.get("category") or "其他"), text, row.get("source_span") or {}))
    return items


async def build_compliance_responses(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> list[ComplianceResponse]:
    """Rebuild the latest version's response list, carrying confirmations by requirement_key."""
    await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        raise NotFoundError("约束集不存在")

    previous = (
        await session.execute(
            select(TenderConstraint)
            .where(
                TenderConstraint.tenant_id == tenant_id,
                TenderConstraint.project_id == project_id,
                TenderConstraint.version < constraint.version,
            )
            .order_by(TenderConstraint.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    current_existing = {
        row.requirement_key: row
        for row in (
            await session.execute(
                select(ComplianceResponse).where(
                    ComplianceResponse.tenant_id == tenant_id,
                    ComplianceResponse.project_id == project_id,
                    ComplianceResponse.constraint_id == constraint.id,
                )
            )
        ).scalars()
    }
    previous_rows: dict[str, ComplianceResponse] = {}
    if previous is not None:
        previous_rows = {
            row.requirement_key: row
            for row in (
                await session.execute(
                    select(ComplianceResponse).where(
                        ComplianceResponse.tenant_id == tenant_id,
                        ComplianceResponse.project_id == project_id,
                        ComplianceResponse.constraint_id == previous.id,
                    )
                )
            ).scalars()
        }

    valid_keys: set[str] = set()
    for kind, category, text, span in _response_items(constraint.payload):
        key = _requirement_key(kind, text)
        if key in current_existing:
            valid_keys.add(key)
            current_existing[key].requirement_text = text
            current_existing[key].category = category
            continue

        carried = previous_rows.get(key)
        session.add(
            ComplianceResponse(
                tenant_id=tenant_id,
                project_id=project_id,
                constraint_id=constraint.id,
                requirement_type=kind,
                requirement_key=key,
                requirement_text=text,
                category=category,
                source_span=span,
                response_status=carried.response_status if carried else "pending",
                response_note=carried.response_note if carried else None,
                evidence_refs=list(carried.evidence_refs or []) if carried else [],
                section_ref=carried.section_ref if carried else None,
                confirmed_by=carried.confirmed_by if carried else None,
                confirmed_at=carried.confirmed_at if carried else None,
            )
        )
        valid_keys.add(key)

    # 对账：最新约束集不再产生的条目（例如跨类型重复）从当前版本移除；旧版本保留供追溯
    for key, row in current_existing.items():
        if key not in valid_keys:
            await session.delete(row)
    await session.commit()
    return list(
        (
            await session.execute(
                select(ComplianceResponse)
                .where(
                    ComplianceResponse.tenant_id == tenant_id,
                    ComplianceResponse.project_id == project_id,
                    ComplianceResponse.constraint_id == constraint.id,
                )
                .order_by(ComplianceResponse.requirement_type, ComplianceResponse.created_at)
            )
        ).scalars()
    )


async def unbuilt_response_item_count(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> int:
    """响应表还没生成时，从最新约束数出应响应条目，免得"不点生成"就绕过导出闸门。"""
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    if constraint is None:
        return 0
    return len(_response_items(constraint.payload))


async def list_compliance_responses(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    constraint_version: int | None = None,
) -> dict[str, Any]:
    await get_project(session, tenant_id, project_id)
    if constraint_version is None:
        constraint = await get_latest_constraint(session, tenant_id, project_id)
    else:
        constraint = (
            await session.execute(
                select(TenderConstraint).where(
                    TenderConstraint.tenant_id == tenant_id,
                    TenderConstraint.project_id == project_id,
                    TenderConstraint.version == constraint_version,
                )
            )
        ).scalar_one_or_none()
        if constraint is None:
            raise NotFoundError("约束版本不存在")

    rows: list[ComplianceResponse] = []
    if constraint is not None:
        rows = list(
            (
                await session.execute(
                    select(ComplianceResponse)
                    .where(
                        ComplianceResponse.tenant_id == tenant_id,
                        ComplianceResponse.project_id == project_id,
                        ComplianceResponse.constraint_id == constraint.id,
                    )
                    .order_by(
                        ComplianceResponse.requirement_type,
                        ComplianceResponse.created_at,
                    )
                )
            ).scalars()
        )
    summary = {status: 0 for status in RESPONSE_STATUSES}
    for row in rows:
        summary[row.response_status] = summary.get(row.response_status, 0) + 1
    return {
        "items": [
            {
                "id": str(row.id),
                "requirement_type": row.requirement_type,
                "requirement_text": row.requirement_text,
                "category": row.category,
                "response_status": row.response_status,
                "response_note": row.response_note,
                "evidence_refs": row.evidence_refs,
                "section_ref": row.section_ref,
                "source_span": row.source_span,
                "confirmed_by": str(row.confirmed_by) if row.confirmed_by else None,
                "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
            }
            for row in rows
        ],
        "summary": summary,
        "total": len(rows),
        "unresolved": summary["pending"] + summary["deviated"] + summary["not_responded"],
        "constraint_version": constraint.version if constraint else None,
    }


async def update_compliance_response(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    response_id: UUID,
    *,
    status: str | None,
    note: str | None,
    evidence_refs: list[str] | None,
    section_ref: str | None,
    user_id: UUID,
) -> ComplianceResponse:
    if status is not None and status not in RESPONSE_STATUSES:
        raise ValidationAppError("无效的响应状态")
    await get_project(session, tenant_id, project_id)
    latest = await get_latest_constraint(session, tenant_id, project_id)
    if latest is None:
        raise ConflictError("约束集不存在，请先上传并解析招标文件")
    row = (
        await session.execute(
            select(ComplianceResponse).where(
                ComplianceResponse.id == response_id,
                ComplianceResponse.tenant_id == tenant_id,
                ComplianceResponse.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("响应条目不存在")
    if row.constraint_id != latest.id:
        raise ConflictError("只能修改最新版本的响应条目，请先应用补遗或重新生成")
    _apply_response_update(
        row,
        status=status,
        note=note,
        evidence_refs=evidence_refs,
        section_ref=section_ref,
        user_id=user_id,
    )
    await session.commit()
    return row


def _apply_response_update(
    row: ComplianceResponse,
    *,
    status: str | None,
    note: str | None,
    evidence_refs: list[str] | None,
    section_ref: str | None,
    user_id: UUID,
) -> None:
    row.response_note = note
    if evidence_refs is not None:
        row.evidence_refs = evidence_refs
    if section_ref is not None:
        row.section_ref = section_ref
    # 只补章节/证据引用不该重刷确认时间，确认口径只跟状态走
    if status is not None:
        row.response_status = status
        if status == "pending":
            row.confirmed_by = None
            row.confirmed_at = None
        else:
            row.confirmed_by = user_id
            row.confirmed_at = datetime.now(UTC)


async def batch_update_compliance_responses(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    response_ids: list[UUID],
    status: str,
    note: str | None,
    user_id: UUID,
) -> int:
    """真实标书常有几十上百条要求，逐条点选不现实；批量标记走同一套确认口径。"""
    if status not in RESPONSE_STATUSES:
        raise ValidationAppError("无效的响应状态")
    await get_project(session, tenant_id, project_id)
    latest = await get_latest_constraint(session, tenant_id, project_id)
    if latest is None:
        raise ConflictError("约束集不存在，请先上传并解析招标文件")
    rows = list(
        (
            await session.execute(
                select(ComplianceResponse).where(
                    ComplianceResponse.tenant_id == tenant_id,
                    ComplianceResponse.project_id == project_id,
                    ComplianceResponse.id.in_(response_ids),
                )
            )
        ).scalars()
    )
    # 混进他人/他项目的 id 必须整体失败，不能静默少更几条让用户以为都标记好了
    if len(rows) != len(set(response_ids)):
        raise NotFoundError("响应条目不存在")
    if any(row.constraint_id != latest.id for row in rows):
        raise ConflictError("只能修改最新版本的响应条目，请先应用补遗或重新生成")
    for row in rows:
        _apply_response_update(
            row,
            status=status,
            note=note if note is not None else row.response_note,
            evidence_refs=None,
            section_ref=row.section_ref,
            user_id=user_id,
        )
    await session.commit()
    return len(rows)


async def run_parse_job(
    job_id: UUID,
    vision_client: OcrProvider | None = None,
    *,
    force: bool = False,
) -> None:
    """ParseJob 后台执行；任务级锁保证后台任务与重启恢复不会重复解析。"""
    async with advisory_lock(f"parse-job:{job_id}") as acquired:
        if not acquired:
            return
        await _run_parse_job_locked(job_id, vision_client, force=force)


async def _run_parse_job_locked(
    job_id: UUID,
    vision_client: OcrProvider | None,
    *,
    force: bool,
) -> None:
    from app.modules.notifications.service import NotificationService

    async with SessionLocal() as session:
        job = await session.get(ParseJob, job_id)
        allowed = {"queued", "failed"} | ({"running"} if force else set())
        if job is None or job.status not in allowed:
            return
        document = await session.get(TenderDocument, job.document_id) if job.document_id else None
        if document is None:
            job.status = "failed"
            job.error = "document missing"
            await session.commit()
            return
        project = await get_project(session, job.tenant_id, job.project_id)
        job.status = "running"
        job.progress = 10
        await session.commit()
        try:
            store = build_snapshot_store(get_settings())
            content = await store.get_bytes(document.object_ref)
            suffix = "." + document.filename.rsplit(".", 1)[-1].lower()
            constraint = await _execute_parse(
                session,
                project=project,
                document=document,
                content=content,
                filename=document.filename,
                suffix=suffix,
                vision_client=vision_client,
                user_id=job.created_by,
            )
            await session.commit()
            job.status = "succeeded"
            job.progress = 100
            job.error = None
            if job.created_by:
                await NotificationService(session).notify(
                    tenant_id=job.tenant_id,
                    user_id=job.created_by,
                    notification_type="parse_job_done",
                    title=f"长文档解析完成：{document.filename}",
                    body="招标文件解析与约束集生成已就绪。",
                    resource_type="parse_job",
                    resource_id=str(job.id),
                )
            _ = constraint
        except Exception as exc:
            await session.rollback()
            job = await session.get(ParseJob, job_id)
            if job is None:
                return
            document = await session.get(TenderDocument, job.document_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            # document 可能已被删除（get 返回 None）：通知文案需要 filename，
            # 放在判空分支内，避免 None.filename 把异常处理本身再打崩。
            if document is not None:
                document.parse_status = "failed"
                document.parse_error = job.error
                if job.created_by:
                    await NotificationService(session).notify(
                        tenant_id=job.tenant_id,
                        user_id=job.created_by,
                        notification_type="parse_job_done",
                        title=f"长文档解析失败：{document.filename}",
                        body=job.error,
                        resource_type="parse_job",
                        resource_id=str(job.id),
                    )
        await session.commit()


async def recover_stale_parse_jobs() -> int:
    """重启恢复：重新驱动卡在 queued/running 的解析任务；任务锁保证不重复。"""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.PARSE_JOB_STALE_MINUTES)
    async with SessionLocal() as session:
        job_ids = list(
            (
                await session.execute(
                    select(ParseJob.id)
                    .where(
                        ParseJob.status.in_(("queued", "running")),
                        ParseJob.created_at < cutoff,
                    )
                    .order_by(ParseJob.created_at)
                    .limit(settings.PARSE_JOB_RECOVERY_BATCH)
                )
            )
            .scalars()
            .all()
        )
    for job_id in job_ids:
        await run_parse_job(job_id, force=True)
    return len(job_ids)


def _diff_item_key(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)[:200]
    for field in ("text", "item", "name", "target", "check_item"):
        value = item.get(field)
        if value:
            return str(value)
    if item.get("event"):
        return f"{item.get('event')}@{item.get('at') or item.get('deadline_at') or ''}"
    if item.get("code"):
        return str(item.get("code"))
    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)[:200]


def _diff_canonical(item: Any) -> str:
    if not isinstance(item, dict):
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    cleaned = {
        key: value
        for key, value in item.items()
        if key not in {"source_span", "confidence"}
    }
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)


def _diff_list(old_items: list[Any], new_items: list[Any]) -> dict[str, Any]:
    old_map = {_diff_item_key(item): item for item in old_items if isinstance(item, dict)}
    new_map = {_diff_item_key(item): item for item in new_items if isinstance(item, dict)}
    added = [key[:200] for key in new_map.keys() - old_map.keys()]
    removed = [key[:200] for key in old_map.keys() - new_map.keys()]
    modified: list[dict[str, Any]] = []
    for key in old_map.keys() & new_map.keys():
        if _diff_canonical(old_map[key]) == _diff_canonical(new_map[key]):
            continue
        old_item, new_item = old_map[key], new_map[key]
        changed_fields = [
            field
            for field in set(old_item) | set(new_item)
            if field not in {"source_span", "confidence"}
            and _diff_canonical({field: old_item.get(field)})
            != _diff_canonical({field: new_item.get(field)})
        ]
        modified.append(
            {
                "key": key[:200],
                "changed_fields": sorted(changed_fields),
                "from": old_item,
                "to": new_item,
            }
        )
    return {"added": added, "removed": removed, "modified": modified}


async def document_version_diff(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID
) -> dict[str, Any]:
    """最近两版约束集的增删改报告；变更项对应响应/清单需人工确认后 apply。"""
    constraints = (
        await session.execute(
            select(TenderConstraint)
            .where(
                TenderConstraint.tenant_id == tenant_id,
                TenderConstraint.project_id == project_id,
            )
            .order_by(TenderConstraint.version.desc())
            .limit(2)
        )
    ).scalars().all()
    if len(constraints) < 2:
        return {"has_diff": False, "reason": "版本数不足 2"}

    old, new = constraints[1], constraints[0]
    document_ids = {old.document_id, new.document_id}
    documents = {
        document.id: document
        for document in (
            await session.execute(
                select(TenderDocument).where(
                    TenderDocument.tenant_id == tenant_id,
                    TenderDocument.id.in_(document_ids),
                )
            )
        ).scalars()
    }

    old_budget = (old.payload.get("budget") or {}).get("amount")
    new_budget = (new.payload.get("budget") or {}).get("amount")
    report: dict[str, Any] = {
        "has_diff": True,
        "from_version": old.version,
        "to_version": new.version,
        "from_document_kind": documents[old.document_id].document_kind
        if old.document_id in documents
        else None,
        "to_document_kind": documents[new.document_id].document_kind
        if new.document_id in documents
        else None,
        "budget": {
            "from": str(old_budget) if old_budget is not None else None,
            "to": str(new_budget) if new_budget is not None else None,
            "changed": old_budget != new_budget,
        },
        "bid_validity_days": {
            "from": old.payload.get("bid_validity_days"),
            "to": new.payload.get("bid_validity_days"),
            "changed": old.payload.get("bid_validity_days")
            != new.payload.get("bid_validity_days"),
        },
    }
    dimensions = (
        "reject_clauses",
        "scoring_items",
        "qualification_requirements",
        "substantive_requirements",
        "clarifications",
        "format_rules",
        "timeline",
        "submission_checklist",
        "technical_parameters",
        "front_appendix",
        "star_clauses",
    )
    for dimension in dimensions:
        report[dimension] = _diff_list(
            old.payload.get(dimension) or [], new.payload.get(dimension) or []
        )
    report["dark_bid"] = {
        "changed": _diff_canonical(old.payload.get("dark_bid") or {})
        != _diff_canonical(new.payload.get("dark_bid") or {}),
        "from": old.payload.get("dark_bid") or {},
        "to": new.payload.get("dark_bid") or {},
    }
    report["packages"] = {
        "changed": _diff_canonical(old.payload.get("packages") or {})
        != _diff_canonical(new.payload.get("packages") or {}),
        "from": old.payload.get("packages") or {},
        "to": new.payload.get("packages") or {},
    }
    report["has_changes"] = bool(
        report["budget"]["changed"]
        or report["bid_validity_days"]["changed"]
        or report["dark_bid"]["changed"]
        or report["packages"]["changed"]
        or any(
            report[dimension]["added"]
            or report[dimension]["removed"]
            or report[dimension]["modified"]
            for dimension in dimensions
        )
    )
    return report


async def apply_document_diff(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    confirm: bool,
    user_id: UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """人工确认后把最新补遗应用到逐条响应；不做自动重跑。"""
    if not confirm:
        raise ValidationAppError("必须确认已核对版本差异后再应用")
    latest = await get_latest_constraint(session, tenant_id, project_id)
    if latest is None:
        raise NotFoundError("约束集不存在")
    document = (
        await session.execute(
            select(TenderDocument).where(
                TenderDocument.tenant_id == tenant_id,
                TenderDocument.id == latest.document_id,
            )
        )
    ).scalar_one_or_none()
    if document is None or document.document_kind != "amendment":
        raise ConflictError("最新版本不是补遗/更正，无需应用")
    diff = await document_version_diff(session, tenant_id=tenant_id, project_id=project_id)
    if not diff.get("has_changes"):
        raise ConflictError("最新补遗没有可应用的变更")
    rows = await build_compliance_responses(session, tenant_id, project_id)
    return {
        "applied": True,
        "constraint_version": latest.version,
        "responses": len(rows),
        "has_changes": diff.get("has_changes", False),
        "note": note,
    }
