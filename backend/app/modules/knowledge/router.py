from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.db.session import get_session
from app.modules.access.service import AuthzService
from app.modules.assets.repository import CompanyRepository
from app.modules.audit.service import AuditService
from app.modules.generation.models import BidSection
from app.modules.generation.schemas import SectionResponse
from app.modules.identity.deps import (
    get_current_user,
    require_all_permissions,
    require_permission,
    require_permission_or_org,
)
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge import kb
from app.modules.knowledge.access import (
    CLEARANCE_RANK,
    can_access_kb,
    ensure_default_kb,
    grant_acl,
    list_acl,
    raise_access_denied,
    revoke_acl,
)
from app.modules.knowledge.models import KnowledgeBase
from app.modules.knowledge.schemas import (
    AclGrantRequest,
    AclResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeDocumentDetail,
    KnowledgeDocumentResponse,
    KnowledgeDocumentUpdate,
    KnowledgeImageResponse,
    KnowledgeImageUpdate,
    KnowledgePackCreate,
    KnowledgePackResponse,
    KnowledgePackUpdate,
    KnowledgeSearchResponse,
    MaterialItem,
    TemplateDraftRequest,
    TemplateSelection,
    WorkspaceResponse,
)
from app.modules.knowledge.service import (
    SKILL_CATALOG,
    apply_template,
    create_pack,
    list_materials,
    list_packs,
    update_pack,
    workspace,
)
from app.modules.knowledge.template_autogen import generate_template_draft
from app.modules.projects.models import BidProject, ScoringItem
from app.modules.projects.schemas import ProjectResponse

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


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


async def _load_kb(session: AsyncSession, tenant_id: UUID, kb_id: UUID) -> KnowledgeBase:
    kb_row = (
        await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id == kb_id
            )
        )
    ).scalar_one_or_none()
    if kb_row is None:
        raise NotFoundError()
    return kb_row


async def _require_kb_manage(
    session: AsyncSession, user: CurrentUser, kb_row: KnowledgeBase
) -> None:
    if not await can_access_kb(session, kb_row, user, "manage"):
        raise_access_denied(kb_row, user)


@router.get("/knowledge/bases", response_model=list[KnowledgeBaseResponse])
async def get_knowledge_bases(
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeBase]:
    await ensure_default_kb(session, user.tenant_id, user.id)
    await session.commit()
    rows = list(
        (
            await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.tenant_id == user.tenant_id)
                .order_by(KnowledgeBase.created_at)
            )
        )
        .scalars()
        .all()
    )
    visible: list[KnowledgeBase] = []
    for row in rows:
        if await can_access_kb(session, row, user, "read"):
            visible.append(row)
    return visible


@router.post("/knowledge/bases", response_model=KnowledgeBaseResponse, status_code=201)
async def post_knowledge_base(
    body: KnowledgeBaseCreate,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBase:
    if CLEARANCE_RANK.get(body.classification, 99) > CLEARANCE_RANK.get(user.clearance, 0):
        raise ForbiddenError("密级高于当前用户 clearance")
    org_unit_id = None
    if body.scope == "tenant":
        if not AuthzService.can_capability(user, "knowledge:manage").allow:
            raise ForbiddenError("missing permission: knowledge:manage")
        visibility: str = body.visibility
        owner_id = None
    elif body.scope == "org_unit":
        if body.org_unit_id is None:
            raise ValidationAppError("部门知识库缺少 org_unit_id")
        if not (
            AuthzService.can_capability(user, "org:manage").allow
            or body.org_unit_id in user.managed_org_unit_ids
        ):
            raise ForbiddenError("missing permission: department manage")
        visibility = "team"
        owner_id = None
        org_unit_id = body.org_unit_id
    else:
        visibility = "private"
        owner_id = user.id
    row = KnowledgeBase(
        tenant_id=user.tenant_id,
        scope=body.scope,
        owner_id=owner_id,
        org_unit_id=org_unit_id,
        name=body.name.strip(),
        description=body.description,
        classification=body.classification,
        visibility=visibility,
        status="active",
        owner_status="active",
        created_by=user.id,
    )
    session.add(row)
    await session.flush()
    await _audit(
        session,
        user,
        "knowledge_base.create",
        "knowledge_base",
        str(row.id),
        {"scope": row.scope, "name": row.name},
    )
    return row


@router.get("/knowledge/acl-subjects")
async def get_acl_subjects(
    user: CurrentUser = Depends(require_permission_or_org("knowledge:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, object]]]:
    from app.modules.identity.models import User
    from app.modules.org.models import OrgMember, OrgUnit

    global_manage = AuthzService.can_capability(user, "knowledge:manage").allow
    visible_org_ids: set[UUID] | None = None
    if not global_manage:
        visible_org_ids = set(user.managed_org_unit_ids)
        visible_org_ids.update(
            org_id
            for org_id, _ in user.org_capabilities
            if AuthzService.can_capability_in_org(
                user, org_id, "knowledge:manage"
            ).allow
        )
        if not visible_org_ids:
            return {"users": [], "org_units": []}

    user_statement = select(User).where(
        User.tenant_id == user.tenant_id, User.status == "active"
    )
    if visible_org_ids is not None:
        user_statement = (
            user_statement.join(OrgMember, OrgMember.user_id == User.id)
            .where(OrgMember.org_unit_id.in_(visible_org_ids))
            .distinct()
        )
    users = list(
        (
            await session.execute(user_statement.order_by(User.display_name))
        )
        .scalars()
        .all()
    )
    unit_statement = select(OrgUnit).where(
        OrgUnit.tenant_id == user.tenant_id, OrgUnit.status == "active"
    )
    if visible_org_ids is not None:
        unit_statement = unit_statement.where(OrgUnit.id.in_(visible_org_ids))
    units = list(
        (
            await session.execute(
                unit_statement.order_by(OrgUnit.sort_order, OrgUnit.name)
            )
        )
        .scalars()
        .all()
    )
    return {
        "users": [
            {"id": str(row.id), "display_name": row.display_name, "email": row.email}
            for row in users
        ],
        "org_units": [
            {
                "id": str(row.id),
                "name": row.name,
                "depth": max(0, row.path.count("/") - 2),
            }
            for row in units
        ],
    }


@router.get("/knowledge/bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBase:
    row = await _load_kb(session, user.tenant_id, kb_id)
    if not await can_access_kb(session, row, user, "read"):
        raise_access_denied(row, user)
    return row


@router.get("/knowledge/bases/{kb_id}/acl", response_model=list[AclResponse])
async def get_kb_acl(
    kb_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list:
    row = await _load_kb(session, user.tenant_id, kb_id)
    await _require_kb_manage(session, user, row)
    return await list_acl(session, user.tenant_id, "knowledge_base", kb_id)


@router.post("/knowledge/bases/{kb_id}/template-draft", response_model=KnowledgePackResponse, status_code=201)
async def post_kb_template_draft(
    kb_id: UUID,
    body: TemplateDraftRequest | None = None,
    user: CurrentUser = Depends(require_permission("template:write")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgePackResponse:
    """从该知识库的历史标书确定性生成结构模板草稿（draft，需人工确认发布）。"""
    pack = await generate_template_draft(session, user, kb_id)
    await _audit(
        session,
        user,
        "knowledge_pack.generate_draft",
        "knowledge_pack",
        str(pack.id),
        {
            "code": pack.code,
            "kb_id": str(kb_id),
            "sections": len(pack.content_json.get("sections", [])),
        },
    )
    return KnowledgePackResponse.model_validate(pack)


@router.post("/knowledge/bases/{kb_id}/acl", response_model=AclResponse, status_code=201)
async def post_kb_acl(
    kb_id: UUID,
    body: AclGrantRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> object:
    row = await _load_kb(session, user.tenant_id, kb_id)
    await _require_kb_manage(session, user, row)
    if row.scope == "personal":
        raise ValidationAppError("个人知识库 P1 不支持共享")
    acl = await grant_acl(
        session,
        tenant_id=user.tenant_id,
        resource_type="knowledge_base",
        resource_id=kb_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        role=body.role,
        granted_by=user.id,
        include_descendants=body.include_descendants,
        expires_at=body.expires_at,
    )
    await _audit(
        session,
        user,
        "knowledge_base.acl.grant",
        "knowledge_base",
        str(kb_id),
        {"acl_id": str(acl.id), "subject_type": body.subject_type, "role": body.role},
    )
    return acl


@router.delete("/knowledge/bases/{kb_id}/acl/{acl_id}", status_code=204)
async def delete_kb_acl(
    kb_id: UUID,
    acl_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await _load_kb(session, user.tenant_id, kb_id)
    await _require_kb_manage(session, user, row)
    await revoke_acl(
        session,
        user.tenant_id,
        acl_id,
        resource_type="knowledge_base",
        resource_id=kb_id,
    )
    await _audit(
        session,
        user,
        "knowledge_base.acl.revoke",
        "knowledge_base",
        str(kb_id),
        {"acl_id": str(acl_id)},
    )


@router.get("/knowledge/documents/{document_id}/acl", response_model=list[AclResponse])
async def get_document_acl(
    document_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list:
    document = await kb.get_document_for_acl(session, user, document_id)
    await _require_kb_manage(session, user, document[1])
    return await list_acl(session, user.tenant_id, "knowledge_document", document_id)


@router.post(
    "/knowledge/documents/{document_id}/acl",
    response_model=AclResponse,
    status_code=201,
)
async def post_document_acl(
    document_id: UUID,
    body: AclGrantRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> object:
    document, kb_row = await kb.get_document_for_acl(session, user, document_id)
    await _require_kb_manage(session, user, kb_row)
    if kb_row.scope == "personal":
        raise ValidationAppError("个人知识库 P1 不支持文档级共享")
    acl = await grant_acl(
        session,
        tenant_id=user.tenant_id,
        resource_type="knowledge_document",
        resource_id=document_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        role=body.role,
        granted_by=user.id,
        include_descendants=body.include_descendants,
        expires_at=body.expires_at,
    )
    await _audit(
        session,
        user,
        "knowledge_document.acl.grant",
        "knowledge_document",
        str(document_id),
        {"acl_id": str(acl.id), "subject_type": body.subject_type, "role": body.role},
    )
    return acl


@router.delete("/knowledge/documents/{document_id}/acl/{acl_id}", status_code=204)
async def delete_document_acl(
    document_id: UUID,
    acl_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _, kb_row = await kb.get_document_for_acl(session, user, document_id)
    await _require_kb_manage(session, user, kb_row)
    await revoke_acl(
        session,
        user.tenant_id,
        acl_id,
        resource_type="knowledge_document",
        resource_id=document_id,
    )
    await _audit(
        session,
        user,
        "knowledge_document.acl.revoke",
        "knowledge_document",
        str(document_id),
        {"acl_id": str(acl_id)},
    )


@router.get("/knowledge-packs", response_model=list[KnowledgePackResponse])
async def get_packs(
    category: str | None = None,
    user: CurrentUser = Depends(require_permission("template:read")),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgePackResponse]:
    packs = await list_packs(session, user, category)
    return [KnowledgePackResponse.model_validate(pack) for pack in packs]


@router.post("/knowledge-packs", response_model=KnowledgePackResponse, status_code=201)
async def post_pack(
    body: KnowledgePackCreate,
    user: CurrentUser = Depends(require_permission("template:write")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgePackResponse:
    pack = await create_pack(session, user, body)
    await _audit(
        session,
        user,
        "knowledge_pack.create",
        "knowledge_pack",
        str(pack.id),
        {
            "code": pack.code,
            "scope": pack.scope,
        },
    )
    return KnowledgePackResponse.model_validate(pack)


@router.patch("/knowledge-packs/{pack_id}", response_model=KnowledgePackResponse)
async def patch_pack(
    pack_id: UUID,
    body: KnowledgePackUpdate,
    user: CurrentUser = Depends(require_permission("template:write")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgePackResponse:
    pack = await update_pack(session, user, pack_id, body)
    data: dict[str, object] = {"enabled": pack.enabled}
    if body.status is not None:
        data["status"] = pack.status
    await _audit(
        session,
        user,
        "knowledge_pack.update",
        "knowledge_pack",
        str(pack.id),
        data,
    )
    return KnowledgePackResponse.model_validate(pack)


@router.put("/projects/{project_id}/template")
async def put_project_template(
    project_id: UUID,
    body: TemplateSelection,
    user: CurrentUser = Depends(
        require_all_permissions("project:write", "template:use")
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    project, sections = await apply_template(
        session,
        user,
        project_id,
        body.template_code,
        body.format_code,
    )
    await _audit(
        session,
        user,
        "project.template.apply",
        "bid_project",
        str(project_id),
        {"template_code": body.template_code, "format_code": body.format_code},
    )
    return {
        "project": ProjectResponse.model_validate(project),
        "sections": [SectionResponse.model_validate(section) for section in sections],
    }


@router.get("/projects/{project_id}/materials", response_model=list[MaterialItem])
async def get_project_materials(
    project_id: UUID,
    user: CurrentUser = Depends(
        require_all_permissions("project:read", "knowledge:use")
    ),
    session: AsyncSession = Depends(get_session),
) -> list[MaterialItem]:
    return await list_materials(session, user, project_id)


@router.get("/projects/{project_id}/workspace", response_model=WorkspaceResponse)
async def get_project_workspace(
    project_id: UUID,
    user: CurrentUser = Depends(
        require_all_permissions("project:read", "knowledge:use")
    ),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    return await workspace(session, user, project_id)


@router.get("/skills")
async def get_skills(
    user: CurrentUser = Depends(require_permission("template:read")),
) -> list[dict[str, str]]:
    return SKILL_CATALOG


def _split_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.replace("，", ",").split(",") if tag.strip()][:20]


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValidationAppError(f"file exceeds {max_bytes // (1024 * 1024)}MB")
    return content


@router.post("/knowledge/documents", response_model=KnowledgeDocumentResponse)
async def upload_knowledge_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    kind: str = Form(default="other"),
    tags: str = Form(default=""),
    company_id: UUID | None = Form(default=None),
    kb_id: UUID | None = Form(default=None),
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    content = await _read_upload(file, kb.MAX_DOCUMENT_BYTES)
    kb_row = (
        await _load_kb(session, user.tenant_id, kb_id)
        if kb_id is not None
        else await ensure_default_kb(session, user.tenant_id, user.id)
    )
    if not await can_access_kb(session, kb_row, user, "write"):
        raise_access_denied(kb_row, user)
    if company_id is not None:
        await CompanyRepository(session, user.tenant_id).get_or_404(company_id)
    document = await kb.ingest_document(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        kb_id=kb_row.id,
        classification=kb_row.classification,
        company_id=company_id,
        title=title,
        kind=kind,
        tags=_split_tags(tags),
        filename=file.filename or "document",
        content_type=file.content_type,
        content=content,
    )
    await _audit(
        session,
        user,
        "knowledge.upload",
        "knowledge_document",
        str(document.id),
        {
            "title": document.title,
            "kind": document.kind,
            "size_bytes": document.size_bytes,
            "kb_id": str(document.kb_id),
        },
    )
    return kb.document_payload(document)


@router.get("/knowledge/documents", response_model=list[KnowledgeDocumentResponse])
async def list_knowledge_documents(
    kb_id: UUID | None = Query(default=None),
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    documents = await kb.list_documents(session, user, kb_id=kb_id)
    return [kb.document_payload(document) for document in documents]


@router.patch("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentResponse)
async def patch_knowledge_document(
    document_id: UUID,
    body: KnowledgeDocumentUpdate,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    document = await kb.update_document(
        session,
        user,
        document_id,
        title=body.title,
        kind=body.kind,
        tags=body.tags,
    )
    await _audit(
        session,
        user,
        "knowledge.update",
        "knowledge_document",
        str(document.id),
        {"title": document.title, "kind": document.kind, "tags": document.tags},
    )
    return kb.document_payload(document)


@router.delete("/knowledge/documents/{document_id}", status_code=204)
async def remove_knowledge_document(
    document_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await kb.delete_document(session, user, document_id)
    await _audit(
        session, user, "knowledge.delete", "knowledge_document", str(document_id), {}
    )


@router.post("/knowledge/images", response_model=KnowledgeImageResponse)
async def upload_knowledge_image(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    caption: str | None = Form(default=None),
    tags: str = Form(default=""),
    sensitive: bool = Form(default=False),
    company_id: UUID | None = Form(default=None),
    kb_id: UUID | None = Form(default=None),
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    content = await _read_upload(file, kb.MAX_IMAGE_BYTES)
    kb_row = (
        await _load_kb(session, user.tenant_id, kb_id)
        if kb_id is not None
        else await ensure_default_kb(session, user.tenant_id, user.id)
    )
    if not await can_access_kb(session, kb_row, user, "write"):
        raise_access_denied(kb_row, user)
    if company_id is not None:
        await CompanyRepository(session, user.tenant_id).get_or_404(company_id)
    image = await kb.ingest_image(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        kb_id=kb_row.id,
        classification=kb_row.classification,
        company_id=company_id,
        title=title,
        caption=caption,
        tags=_split_tags(tags),
        sensitive=sensitive,
        filename=file.filename or "image",
        content_type=file.content_type,
        content=content,
    )
    await _audit(
        session,
        user,
        "knowledge.upload",
        "knowledge_image",
        str(image.id),
        {
            "title": image.title,
            "sensitive": image.sensitive,
            "kb_id": str(image.kb_id),
        },
    )
    return kb.image_payload(image)


@router.get("/knowledge/images", response_model=list[KnowledgeImageResponse])
async def list_knowledge_images(
    kb_id: UUID | None = Query(default=None),
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    images = await kb.list_images(session, user, kb_id=kb_id)
    return [kb.image_payload(image) for image in images]


@router.delete("/knowledge/images/{image_id}", status_code=204)
async def remove_knowledge_image(
    image_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await kb.delete_image(session, user, image_id)
    await _audit(session, user, "knowledge.delete", "knowledge_image", str(image_id), {})


@router.get("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(default=4, ge=1, le=20),
    kb_id: UUID | None = Query(default=None),
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    result = await kb.search(session, user, q, limit=limit, kb_id=kb_id)
    return KnowledgeSearchResponse(query=q, chunks=result["chunks"], images=result["images"])


@router.get("/knowledge/images/{image_id}/content")
async def get_knowledge_image_content(
    image_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    image, payload = await kb.read_image_bytes(session, user, image_id)
    # media_type 由服务端入库时的魔数校验结果决定，绝不回写上传表单的 content_type；
    # attachment 避免浏览器把异常内容按同源 HTML 渲染。
    media_type = (image.content_type or "").lower()
    if media_type not in {"image/png", "image/jpeg", "image/webp"}:
        media_type = "application/octet-stream"
    filename = image.filename.replace('"', "")[:120] or "image"
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentDetail)
async def get_knowledge_document(
    document_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    document, chunks = await kb.get_document_detail(session, user, document_id)
    payload = kb.document_payload(document)
    payload["chunks"] = [
        {
            "chunk_index": chunk.chunk_index,
            "heading": chunk.heading,
            "text": chunk.text,
            "keywords": chunk.keywords or [],
        }
        for chunk in chunks
    ]
    payload["text_preview"] = (document.text_content or "")[:1200]
    return payload


@router.get(
    "/projects/{project_id}/sections/{section_id}/knowledge-context",
    response_model=KnowledgeSearchResponse,
)
async def get_section_knowledge_context(
    project_id: UUID,
    section_id: UUID,
    user: CurrentUser = Depends(
        require_all_permissions("project:read", "knowledge:use")
    ),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    section = (
        await session.execute(
            select(BidSection).where(
                BidSection.tenant_id == user.tenant_id,
                BidSection.project_id == project_id,
                BidSection.id == section_id,
            )
        )
    ).scalar_one_or_none()
    if section is None:
        raise NotFoundError()
    project = (
        await session.execute(
            select(BidProject).where(
                BidProject.tenant_id == user.tenant_id, BidProject.id == project_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError()
    scoring_text = ""
    if section.scoring_item_id is not None:
        scoring_item = await session.get(ScoringItem, section.scoring_item_id)
        if (
            scoring_item is not None
            and scoring_item.tenant_id == user.tenant_id
            and scoring_item.project_id == project_id
        ):
            scoring_text = f"{scoring_item.item} {scoring_item.criteria or ''}".strip()
    query = kb.section_query(
        title=section.title,
        scoring_text=scoring_text,
        project_name=project.name,
        buyer=project.buyer or "",
        code=project.code or "",
    )
    result = await kb.search(
        session, user, query, limit=4, include_sensitive=False, action="use"
    )
    return KnowledgeSearchResponse(query=query, chunks=result["chunks"], images=result["images"])


@router.patch("/knowledge/images/{image_id}", response_model=KnowledgeImageResponse)
async def patch_knowledge_image(
    image_id: UUID,
    body: KnowledgeImageUpdate,
    user: CurrentUser = Depends(require_permission_or_org("knowledge:write")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    image = await kb.update_image(
        session,
        user,
        image_id,
        title=body.title,
        caption=body.caption,
        tags=body.tags,
        sensitive=body.sensitive,
    )
    await _audit(
        session,
        user,
        "knowledge.update",
        "knowledge_image",
        str(image.id),
        {"title": image.title, "sensitive": image.sensitive},
    )
    return kb.image_payload(image)
