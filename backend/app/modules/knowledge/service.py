from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.audit.service import AuditService
from app.modules.compliance.access_service import active_break_glass_resource_ids
from app.modules.compliance.models import ComplianceReport
from app.modules.generation.models import BidSection
from app.modules.generation.service import build_outline
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import CLEARANCE_RANK
from app.modules.knowledge.builtin import ensure_builtin_packs
from app.modules.knowledge.kb import list_documents as list_visible_documents
from app.modules.knowledge.models import KnowledgePack
from app.modules.knowledge.schemas import (
    KnowledgePackCreate,
    KnowledgePackUpdate,
    MaterialItem,
    WorkspaceResponse,
    WorkspaceStage,
)
from app.modules.knowledge.template_access import (
    can_manage_templates,
    can_read_template_with_break_glass,
    template_visibility_conditions,
)
from app.modules.projects.models import BidProject
from app.modules.projects.service import (
    get_latest_constraint,
    get_project,
    load_assets_for_user,
)

SKILL_CATALOG = [
    {
        "code": "tender_parse",
        "name": "招标文件解析",
        "category": "parse",
        "status": "ready",
        "description": "docx/pdf/扫描件 OCR → 约束集、评分项、资格、时间、预算、清单。",
    },
    {
        "code": "qualification",
        "name": "资格预审",
        "category": "decision",
        "status": "ready",
        "description": "逐条 PASS/FAIL/UNKNOWN，缺证据不伪造通过。",
    },
    {
        "code": "bid_decision",
        "name": "Bid-No-Bid",
        "category": "decision",
        "status": "ready",
        "description": "资格、矩阵覆盖、预算与风险因子输出 Go/Conditional Go/No-Go。",
    },
    {
        "code": "structure_template",
        "name": "结构模板",
        "category": "compose",
        "status": "ready",
        "description": "政采 IT/货物、工程、企业通用模板，合并评分项生成大纲。",
    },
    {
        "code": "material_check",
        "name": "素材清单",
        "category": "compose",
        "status": "ready",
        "description": "按模板要求比对资质、人员、业绩，标记缺失与待编写材料。",
    },
    {
        "code": "llm_generation",
        "name": "章节生成",
        "category": "produce",
        "status": "ready",
        "description": "OpenAI 兼容网关；未配置时明确失败，不静默降级。",
    },
    {
        "code": "factcheck",
        "name": "主张级核查",
        "category": "produce",
        "status": "ready",
        "description": "金额/日期/比例/工期/标准逐条匹配来源，无源标红。",
    },
    {
        "code": "compliance",
        "name": "确定性合规",
        "category": "deliver",
        "status": "ready",
        "description": "暗标、格式、资格、证据、清单、时间、空章节规则扫描。",
    },
    {
        "code": "dark_bid",
        "name": "暗标脱敏",
        "category": "deliver",
        "status": "ready",
        "description": "企业/人员名称替换与文档元数据清理。",
    },
    {
        "code": "docx_export",
        "name": "格式排版导出",
        "category": "deliver",
        "status": "ready",
        "description": "按格式包输出 docx；审批后才可导出。",
    },
    {
        "code": "ai_evaluation",
        "name": "AI 评标模拟",
        "category": "review",
        "status": "ready",
        "description": "判别模型逐项评分、证据、缺口与整改建议。",
    },
    {
        "code": "similarity",
        "name": "相似度 / 报价异常",
        "category": "review",
        "status": "ready",
        "description": "跨章节重复段落、超预算与异常低价确定性检查；不调用 LLM。",
    },
]


async def list_packs(
    session: AsyncSession,
    user: CurrentUser,
    category: str | None = None,
) -> list[KnowledgePack]:
    await ensure_builtin_packs(session)
    await session.commit()
    statement = select(KnowledgePack).where(
        KnowledgePack.owner_status == "active",
        KnowledgePack.enabled.is_(True),
        template_visibility_conditions(user),
    )
    if category:
        statement = statement.where(KnowledgePack.category == category)
    packs = list(
        (await session.execute(statement.order_by(KnowledgePack.category, KnowledgePack.name)))
        .scalars()
        .all()
    )
    visible = [
        pack
        for pack in packs
        if await can_read_template_with_break_glass(session, pack, user)
    ]
    seen = {pack.id for pack in visible}
    extra_ids = await active_break_glass_resource_ids(
        session, user=user, resource_type="knowledge_pack"
    )
    if extra_ids:
        extra_statement = select(KnowledgePack).where(
            KnowledgePack.tenant_id == user.tenant_id,
            KnowledgePack.id.in_(extra_ids),
            KnowledgePack.owner_status == "active",
            KnowledgePack.enabled.is_(True),
        )
        extra = list((await session.execute(extra_statement)).scalars().all())
        for pack in extra:
            if pack.id not in seen and await can_read_template_with_break_glass(
                session, pack, user
            ):
                visible.append(pack)
    return visible


async def get_pack(
    session: AsyncSession, user: CurrentUser, code: str
) -> KnowledgePack | None:
    pack = (
        await session.execute(
            select(KnowledgePack).where(KnowledgePack.code == code)
        )
    ).scalar_one_or_none()
    if pack is None or not await can_read_template_with_break_glass(session, pack, user):
        return None
    return pack


async def create_pack(
    session: AsyncSession, user: CurrentUser, body: KnowledgePackCreate
) -> KnowledgePack:
    _validate_content(body.category, body.content_json)
    scope = body.scope or ("tenant" if can_manage_templates(user) else "personal")
    if scope == "tenant" and not can_manage_templates(user):
        raise ForbiddenError("missing permission: template:publish")
    if scope == "tenant" and CLEARANCE_RANK.get(body.classification, 99) > CLEARANCE_RANK.get(
        user.clearance, 0
    ):
        raise ForbiddenError("模板密级高于当前用户 clearance")
    code = f"custom.{uuid4().hex[:10]}"
    pack = KnowledgePack(
        tenant_id=user.tenant_id,
        code=code,
        name=body.name,
        category=body.category,
        description=body.description,
        content_json=body.content_json,
        version="1.0",
        is_builtin=False,
        enabled=True,
        scope=scope,
        owner_id=user.id if scope == "personal" else None,
        classification=body.classification,
        status="published",
        owner_status="active",
        published_by=user.id,
        published_at=datetime.now(UTC),
    )
    session.add(pack)
    await session.commit()
    await session.refresh(pack)
    return pack


def _validate_content(category: str, content: dict[str, Any]) -> None:
    if category == "structure":
        sections = content.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValidationAppError("结构模板至少需要一个章节")
        for section in sections:
            if not isinstance(section, dict) or not str(section.get("title") or "").strip():
                raise ValidationAppError("结构模板的每个章节都需要标题")
            materials = section.get("required_materials", [])
            if not isinstance(materials, list) or any(
                not isinstance(item, str) for item in materials
            ):
                raise ValidationAppError("required_materials 必须是字符串列表")
    elif category == "format":
        if not isinstance(content.get("page"), dict) or not isinstance(content.get("body"), dict):
            raise ValidationAppError("格式模板必须包含 page 与 body 对象")
    elif not isinstance(content, dict):
        raise ValidationAppError("模板内容必须是对象")


async def update_pack(
    session: AsyncSession, user: CurrentUser, pack_id: UUID, body: KnowledgePackUpdate
) -> KnowledgePack:
    pack = (
        await session.execute(
            select(KnowledgePack).where(KnowledgePack.id == pack_id)
        )
    ).scalar_one_or_none()
    if pack is None:
        raise NotFoundError()
    if pack.scope == "system" or pack.is_builtin:
        raise NotFoundError()
    if pack.tenant_id != user.tenant_id:
        raise NotFoundError()
    if pack.scope == "personal" and pack.owner_id != user.id:
        raise NotFoundError()
    if pack.scope == "tenant" and not can_manage_templates(user):
        raise ForbiddenError("missing permission: template:publish")
    if body.name is not None:
        pack.name = body.name
    if body.description is not None:
        pack.description = body.description
    if body.content_json is not None:
        _validate_content(pack.category, body.content_json)
        pack.content_json = body.content_json
    if body.enabled is not None:
        pack.enabled = body.enabled
    if body.status is not None:
        if body.status == "published" and not can_manage_templates(user):
            raise ForbiddenError("missing permission: template:publish")
        pack.status = body.status
        if body.status == "published":
            pack.published_by = user.id
            pack.published_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(pack)
    return pack


async def apply_template(
    session: AsyncSession,
    user: CurrentUser,
    project_id: UUID,
    template_code: str,
    format_code: str | None,
) -> tuple[BidProject, list[BidSection]]:
    tenant_id = user.tenant_id
    await ensure_builtin_packs(session)
    await session.commit()
    project = await get_project(session, tenant_id, project_id)
    template = await get_pack(session, user, template_code)
    if template is None or template.category != "structure" or not template.enabled:
        raise ValidationAppError("结构模板不可用")
    # 草稿/已废弃模板不能被项目使用：自动生成（template_autogen）与人工新建草稿
    # 都必须先由具备 template:publish 的人确认发布，否则「草稿」标签就没有约束力。
    if template.status != "published":
        raise ValidationAppError("结构模板尚未发布，请先人工确认后发布")
    if template.scope == "personal" and template.owner_id != user.id:
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="compliance.break_glass.access",
            resource_type="knowledge_pack",
            resource_id=str(template.id),
            data={"owner_id": str(template.owner_id) if template.owner_id else None},
        )
        await session.commit()
    if format_code:
        format_pack = await get_pack(session, user, format_code)
        if format_pack is None or format_pack.category != "format" or not format_pack.enabled:
            raise ValidationAppError("格式模板不可用")
    project.template_code = template.code
    project.format_code = format_code
    await session.flush()
    sections = await build_outline(session, tenant_id, project_id, reset=True)
    await session.refresh(project)
    return project, sections


def _material_kind(name: str) -> str | None:
    if any(token in name for token in ("人员", "项目经理", "工程师", "证书")):
        return "personnel"
    if any(token in name for token in ("业绩", "合同", "案例")):
        return "performances"
    if any(token in name for token in ("资质", "营业执照", "认证", "检测", "授权")):
        return "qualifications"
    if "信用" in name:
        return "credits"
    return None


def _asset_names(assets: dict[str, list[dict]]) -> dict[str, list[str]]:
    return {
        "qualifications": [str(item.get("name") or "") for item in assets["qualifications"]],
        "personnel": [str(item.get("name") or "") for item in assets["personnel"]],
        "performances": [
            str(item.get("name") or item.get("project_name") or "")
            for item in assets["performances"]
        ],
        "credits": [
            str(item.get("name") or item.get("check_item") or "") for item in assets["credits"]
        ],
    }


async def list_materials(
    session: AsyncSession, user: CurrentUser, project_id: UUID
) -> list[MaterialItem]:
    tenant_id = user.tenant_id
    project = await get_project(session, tenant_id, project_id)
    if not project.template_code:
        return []
    pack = await get_pack(session, user, project.template_code)
    if pack is None:
        return []
    assets = _asset_names(
        await load_assets_for_user(session, tenant_id, project.company_id, user=user)
    )
    documents = [
        (document.title, document.tags)
        for document in await list_visible_documents(session, user)
    ]
    result: list[MaterialItem] = []
    seen: set[tuple[str, str]] = set()
    for section in pack.content_json.get("sections", []):
        section_name = str(section.get("title") or "")
        for material in section.get("required_materials", []):
            name = str(material).strip()
            key = (section_name, name)
            if not name or key in seen:
                continue
            seen.add(key)
            tokens = [
                token
                for token in name.replace("（", "、").replace("）", "、").split("、")
                if len(token) >= 2
            ]
            for token in list(tokens):
                for suffix in ("资质", "证书", "合同", "授权书", "报告", "方案", "计划", "承诺"):
                    if token.endswith(suffix) and len(token) > len(suffix) + 1:
                        tokens.append(token[: -len(suffix)])
            knowledge_matches = [
                title
                for title, tags in documents
                if any(
                    token in title
                    or any(token in str(tag) for tag in (tags or []))
                    for token in tokens
                )
            ][:5]
            kind = _material_kind(name)
            if kind is None:
                result.append(
                    MaterialItem(
                        name=name,
                        section=section_name,
                        kind="document",
                        status="to_write",
                        knowledge_matches=knowledge_matches,
                    )
                )
                continue
            names = [value for value in assets.get(kind, []) if value]
            matched = [
                value for value in names if any(token in value or value in name for token in tokens)
            ]
            if matched:
                status = "available"
            elif names:
                status = "unknown"
            else:
                status = "missing"
            result.append(
                MaterialItem(
                    name=name,
                    section=section_name,
                    kind=kind,
                    status=status,
                    matches=matched[:5],
                    knowledge_matches=knowledge_matches,
                )
            )
    return result


async def workspace(
    session: AsyncSession, user: CurrentUser, project_id: UUID
) -> WorkspaceResponse:
    tenant_id = user.tenant_id
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
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
        )
        .scalars()
        .all()
    )
    approved = sum(1 for section in sections if section.status == "approved")
    unsourced = sum(section.unsourced_count for section in sections)
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
    template = (
        await get_pack(session, user, project.template_code) if project.template_code else None
    )
    format_pack = (
        await get_pack(session, user, project.format_code) if project.format_code else None
    )

    parse_blockers = [] if constraint else ["尚未解析招标文件"]
    compose_blockers = [] if sections else ["尚未生成大纲"]
    produce_blockers = []
    if not sections:
        produce_blockers.append("尚未生成章节")
    if approved == 0:
        produce_blockers.append("尚无审批通过的章节")
    deliver_blockers = []
    if approved == 0:
        deliver_blockers.append("导出前需审批章节")
    if report is None:
        deliver_blockers.append("尚未运行合规预检")

    stages = [
        WorkspaceStage(
            key="parse",
            label="解析与决策",
            status="done" if constraint and project.decision_status else "in_progress",
            detail="约束集已生成" if constraint else "等待上传招标文件",
            blockers=parse_blockers,
        ),
        WorkspaceStage(
            key="compose",
            label="模板与大纲",
            status="done" if sections else "pending",
            detail=f"{len(sections)} 个章节",
            blockers=compose_blockers,
        ),
        WorkspaceStage(
            key="produce",
            label="生成与核查",
            status="done"
            if sections and approved == len(sections)
            else "in_progress"
            if sections
            else "pending",
            detail=f"已审批 {approved}/{len(sections)}，无源主张 {unsourced}",
            blockers=produce_blockers,
        ),
        WorkspaceStage(
            key="deliver",
            label="合规与交付",
            status="done"
            if report and approved > 0
            else "in_progress"
            if approved > 0
            else "pending",
            detail="可进行合规预检与导出" if report and approved > 0 else "等待审批与预检",
            blockers=deliver_blockers,
        ),
    ]
    return WorkspaceResponse(
        project_id=project.id,
        template_code=project.template_code,
        format_code=project.format_code,
        template_name=template.name if template else None,
        format_name=format_pack.name if format_pack else None,
        stages=stages,
        sections_total=len(sections),
        sections_approved=approved,
        unsourced_count=unsourced,
    )
