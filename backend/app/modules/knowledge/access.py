from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.core.permissions import has_permission
from app.modules.access.models import ResourceAcl
from app.modules.access.service import AuthzService
from app.modules.compliance.access_service import (
    active_break_glass_resource_ids,
    has_active_break_glass,
)
from app.modules.identity.models import User
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.models import KnowledgeBase, KnowledgeDocument, KnowledgeImage
from app.modules.org.models import OrgUnit

CLEARANCE_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
ROLE_RANK = {"viewer": 1, "user": 2, "editor": 3, "manager": 4}
ACTION_RANK = {"read": 1, "use": 2, "write": 3, "manage": 4}
DEFAULT_KB_NAME = "企业知识库"


@dataclass(frozen=True)
class AclInfo:
    roles: tuple[str, ...] = ()
    has_acl: bool = False


def _has(user: CurrentUser, capability: str) -> bool:
    return AuthzService.can_capability(user, capability).allow


def _has_org(user: CurrentUser, org_unit_id: UUID, capability: str) -> bool:
    for scope_id, capabilities in user.org_capabilities:
        if scope_id == org_unit_id and has_permission(list(capabilities), capability):
            return True
    return False


def _can_read_classification(user: CurrentUser, classification: str) -> bool:
    return CLEARANCE_RANK.get(classification, 99) <= CLEARANCE_RANK.get(user.clearance, 0)


def _role_allows(roles: list[str], action: str) -> bool:
    need = ACTION_RANK.get(action)
    if need is None:
        return False
    return any(ROLE_RANK.get(role, 0) >= need for role in roles)


def _acl_matches(row: ResourceAcl, user: CurrentUser) -> bool:
    if row.subject_type == "user":
        return row.subject_id == user.id
    if row.subject_type == "tenant":
        return row.subject_id == user.tenant_id
    if row.subject_type == "org_unit":
        return row.subject_id in user.org_unit_ids or (
            row.include_descendants and row.subject_id in user.org_unit_ancestor_ids
        )
    return False


async def load_acl_info(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    resource_type: str,
    resource_ids: set[UUID] | list[UUID],
    user: CurrentUser,
) -> dict[UUID, AclInfo]:
    if not resource_ids:
        return {}
    rows = list(
        (
            await session.execute(
                select(ResourceAcl).where(
                    ResourceAcl.tenant_id == tenant_id,
                    ResourceAcl.resource_type == resource_type,
                    ResourceAcl.resource_id.in_(resource_ids),
                    or_(
                        ResourceAcl.expires_at.is_(None),
                        ResourceAcl.expires_at > func.now(),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    roles: dict[UUID, set[str]] = {}
    present: set[UUID] = set()
    for row in rows:
        present.add(row.resource_id)
        if _acl_matches(row, user):
            roles.setdefault(row.resource_id, set()).add(row.role)
    return {
        resource_id: AclInfo(
            roles=tuple(sorted(roles.get(resource_id, set()))),
            has_acl=resource_id in present,
        )
        for resource_id in present
    }


async def _acl_info(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    resource_type: str,
    resource_id: UUID,
    user: CurrentUser,
) -> AclInfo:
    return (
        await load_acl_info(
            session,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_ids={resource_id},
            user=user,
        )
    ).get(resource_id, AclInfo())


async def ensure_default_kb(
    session: AsyncSession, tenant_id: UUID, user_id: UUID | None = None
) -> KnowledgeBase:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"knowledge_default:{tenant_id}"},
    )
    kb = (
        await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.scope == "tenant",
                KnowledgeBase.name == DEFAULT_KB_NAME,
                KnowledgeBase.status == "active",
            )
        )
    ).scalar_one_or_none()
    if kb is not None:
        return kb
    kb = KnowledgeBase(
        tenant_id=tenant_id,
        scope="tenant",
        name=DEFAULT_KB_NAME,
        classification="internal",
        visibility="enterprise",
        status="active",
        owner_status="active",
        created_by=user_id,
    )
    session.add(kb)
    await session.flush()
    return kb


async def can_access_kb(
    session: AsyncSession,
    kb: KnowledgeBase,
    user: CurrentUser,
    action: str,
    *,
    acl_info: AclInfo | None = None,
    break_glass_ids: set[UUID] | None = None,
) -> bool:
    if kb.tenant_id != user.tenant_id or kb.status != "active" or kb.owner_status != "active":
        return False
    has_capability = _has(user, f"knowledge:{action}")
    if kb.scope == "org_unit" and kb.org_unit_id is not None:
        has_capability = has_capability or _has_org(
            user, kb.org_unit_id, f"knowledge:{action}"
        )
    if not has_capability:
        return False
    if kb.scope == "personal":
        if kb.owner_id == user.id:
            return True
        if action != "read":
            return False
        if break_glass_ids is not None:
            return kb.id in break_glass_ids
        return await has_active_break_glass(
            session,
            user=user,
            resource_type="knowledge_base",
            resource_id=kb.id,
        )
    if not _can_read_classification(user, kb.classification):
        return False
    if _has(user, "knowledge:manage") or (
        kb.scope == "org_unit"
        and kb.org_unit_id is not None
        and _has_org(user, kb.org_unit_id, "knowledge:manage")
    ):
        return True
    if kb.scope == "org_unit":
        if kb.org_unit_id is not None and _has_org(
            user, kb.org_unit_id, f"knowledge:{action}"
        ):
            return True
        if kb.org_unit_id in user.managed_org_unit_ids:
            return True
        if action in {"read", "use"} and kb.org_unit_id in user.org_unit_ids:
            return True
    if action in {"read", "use"} and kb.visibility == "enterprise":
        return True
    info = acl_info
    if info is None:
        info = await _acl_info(
            session,
            tenant_id=kb.tenant_id,
            resource_type="knowledge_base",
            resource_id=kb.id,
            user=user,
        )
    return _role_allows(list(info.roles), action)


async def can_access_document(
    session: AsyncSession,
    document: KnowledgeDocument,
    kb: KnowledgeBase,
    user: CurrentUser,
    action: str,
    *,
    doc_acl_info: AclInfo | None = None,
    kb_acl_info: AclInfo | None = None,
    break_glass_ids: set[UUID] | None = None,
) -> bool:
    if document.tenant_id != user.tenant_id or kb.tenant_id != user.tenant_id:
        return False
    has_capability = _has(user, f"knowledge:{action}")
    if kb.scope == "org_unit" and kb.org_unit_id is not None:
        has_capability = has_capability or _has_org(
            user, kb.org_unit_id, f"knowledge:{action}"
        )
    if not has_capability:
        return False
    if kb.scope == "personal":
        if kb.owner_id == user.id:
            return True
        if action != "read":
            return False
        if break_glass_ids is not None:
            return kb.id in break_glass_ids
        return await has_active_break_glass(
            session,
            user=user,
            resource_type="knowledge_base",
            resource_id=kb.id,
        )
    if not _can_read_classification(user, kb.classification):
        return False
    if not _can_read_classification(user, document.classification):
        return False
    info = doc_acl_info
    if info is None:
        info = await _acl_info(
            session,
            tenant_id=document.tenant_id,
            resource_type="knowledge_document",
            resource_id=document.id,
            user=user,
        )
    if info.roles:
        return _role_allows(list(info.roles), action)
    if info.has_acl:
        return False
    return await can_access_kb(
        session,
        kb,
        user,
        action,
        acl_info=kb_acl_info,
        break_glass_ids=break_glass_ids,
    )


async def accessible_kb_ids(
    session: AsyncSession,
    tenant_id: UUID,
    user: CurrentUser,
    action: str = "read",
) -> list[UUID]:
    kbs = list(
        (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == tenant_id,
                    KnowledgeBase.status == "active",
                    KnowledgeBase.owner_status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    if not kbs:
        return []
    kb_ids = {kb.id for kb in kbs}
    acl_map = await load_acl_info(
        session,
        tenant_id=tenant_id,
        resource_type="knowledge_base",
        resource_ids=kb_ids,
        user=user,
    )
    break_glass_ids: set[UUID] = set()
    if any(kb.scope == "personal" and kb.owner_id != user.id for kb in kbs):
        break_glass_ids = set(
            await active_break_glass_resource_ids(
                session, user=user, resource_type="knowledge_base"
            )
        )
    accessible: list[UUID] = []
    for kb in kbs:
        if await can_access_kb(
            session,
            kb,
            user,
            action,
            acl_info=acl_map.get(kb.id, AclInfo()),
            break_glass_ids=break_glass_ids,
        ):
            accessible.append(kb.id)
    return accessible


async def _kb_map(session: AsyncSession, kb_ids: set[UUID]) -> dict[UUID, KnowledgeBase]:
    if not kb_ids:
        return {}
    rows = (
        await session.execute(select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))
    ).scalars().all()
    return {kb.id: kb for kb in rows}


async def filter_documents(
    session: AsyncSession,
    documents: list[KnowledgeDocument],
    user: CurrentUser,
    action: str = "read",
) -> list[KnowledgeDocument]:
    if not documents:
        return []
    kb_ids = {document.kb_id for document in documents}
    kb_map = await _kb_map(session, kb_ids)
    kb_acl = await load_acl_info(
        session,
        tenant_id=user.tenant_id,
        resource_type="knowledge_base",
        resource_ids=kb_ids,
        user=user,
    )
    doc_acl = await load_acl_info(
        session,
        tenant_id=user.tenant_id,
        resource_type="knowledge_document",
        resource_ids={document.id for document in documents},
        user=user,
    )
    break_glass_ids = set(
        await active_break_glass_resource_ids(
            session, user=user, resource_type="knowledge_base"
        )
    )
    result: list[KnowledgeDocument] = []
    for document in documents:
        kb = kb_map.get(document.kb_id)
        if kb is not None and await can_access_document(
            session,
            document,
            kb,
            user,
            action,
            doc_acl_info=doc_acl.get(document.id, AclInfo()),
            kb_acl_info=kb_acl.get(kb.id, AclInfo()),
            break_glass_ids=break_glass_ids,
        ):
            result.append(document)
    return result


async def filter_images(
    session: AsyncSession,
    images: list[KnowledgeImage],
    user: CurrentUser,
    action: str = "read",
) -> list[KnowledgeImage]:
    if not images:
        return []
    kb_ids = {image.kb_id for image in images}
    kb_map = await _kb_map(session, kb_ids)
    kb_acl = await load_acl_info(
        session,
        tenant_id=user.tenant_id,
        resource_type="knowledge_base",
        resource_ids=kb_ids,
        user=user,
    )
    break_glass_ids = set(
        await active_break_glass_resource_ids(
            session, user=user, resource_type="knowledge_base"
        )
    )
    result: list[KnowledgeImage] = []
    for image in images:
        kb = kb_map.get(image.kb_id)
        if (
            kb is not None
            and await can_access_kb(
                session,
                kb,
                user,
                action,
                acl_info=kb_acl.get(kb.id, AclInfo()),
                break_glass_ids=break_glass_ids,
            )
            and _can_read_classification(user, image.classification)
        ):
            result.append(image)
    return result


def raise_access_denied(kb: KnowledgeBase | None, user: CurrentUser | None = None) -> None:
    if kb is None or kb.scope == "personal":
        raise NotFoundError()
    if kb.scope == "org_unit":
        if user is None:
            raise NotFoundError()
        visible = (
            kb.org_unit_id in user.org_unit_ids
            or kb.org_unit_id in user.managed_org_unit_ids
            or _has(user, "knowledge:manage")
        )
        if not visible:
            raise NotFoundError()
    raise ForbiddenError("无权访问该知识库")


async def list_acl(
    session: AsyncSession, tenant_id: UUID, resource_type: str, resource_id: UUID
) -> list[ResourceAcl]:
    return list(
        (
            await session.execute(
                select(ResourceAcl)
                .where(
                    ResourceAcl.tenant_id == tenant_id,
                    ResourceAcl.resource_type == resource_type,
                    ResourceAcl.resource_id == resource_id,
                )
                .order_by(ResourceAcl.created_at)
            )
        )
        .scalars()
        .all()
    )


async def grant_acl(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    resource_type: str,
    resource_id: UUID,
    subject_type: str,
    subject_id: UUID,
    role: str,
    granted_by: UUID,
    include_descendants: bool = False,
    expires_at: datetime | None = None,
) -> ResourceAcl:
    if subject_type not in {"user", "tenant", "org_unit"}:
        raise ValidationAppError("P2 支持 user / tenant / org_unit 主体")
    if role not in ROLE_RANK:
        raise ValidationAppError("无效的 ACL 角色")
    if subject_type == "tenant" and subject_id != tenant_id:
        raise ValidationAppError("tenant 主体必须是当前租户")
    if subject_type == "user":
        subject = await session.get(User, subject_id)
        if subject is None or subject.tenant_id != tenant_id:
            raise ValidationAppError("主体用户不属于当前租户")
    if subject_type == "org_unit":
        unit = await session.get(OrgUnit, subject_id)
        if unit is None or unit.tenant_id != tenant_id:
            raise ValidationAppError("主体部门不属于当前租户")

    acl = (
        await session.execute(
            select(ResourceAcl).where(
                ResourceAcl.tenant_id == tenant_id,
                ResourceAcl.resource_type == resource_type,
                ResourceAcl.resource_id == resource_id,
                ResourceAcl.subject_type == subject_type,
                ResourceAcl.subject_id == subject_id,
            )
        )
    ).scalar_one_or_none()
    if acl is None:
        acl = ResourceAcl(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            subject_type=subject_type,
            subject_id=subject_id,
            role=role,
            granted_by=granted_by,
            include_descendants=include_descendants,
            expires_at=expires_at,
        )
        session.add(acl)
    else:
        acl.role = role
        acl.granted_by = granted_by
        acl.include_descendants = include_descendants
        acl.expires_at = expires_at
    await _bump_acl_version(session, tenant_id, resource_type, resource_id)
    await session.flush()
    return acl


async def revoke_acl(
    session: AsyncSession,
    tenant_id: UUID,
    acl_id: UUID,
    *,
    resource_type: str,
    resource_id: UUID,
) -> None:
    acl = (
        await session.execute(
            select(ResourceAcl).where(
                ResourceAcl.tenant_id == tenant_id,
                ResourceAcl.id == acl_id,
                ResourceAcl.resource_type == resource_type,
                ResourceAcl.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()
    if acl is None:
        raise NotFoundError()
    await session.delete(acl)
    await _bump_acl_version(session, tenant_id, acl.resource_type, acl.resource_id)
    await session.flush()


async def _bump_acl_version(
    session: AsyncSession, tenant_id: UUID, resource_type: str, resource_id: UUID
) -> None:
    if resource_type == "knowledge_base":
        kb = (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id == resource_id
                )
            )
        ).scalar_one_or_none()
        if kb is not None:
            kb.acl_version += 1
    elif resource_type == "knowledge_document":
        document = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant_id,
                    KnowledgeDocument.id == resource_id,
                )
            )
        ).scalar_one_or_none()
        if document is not None:
            document.acl_version += 1
