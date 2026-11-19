from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.service import AuthzService
from app.modules.compliance.access_service import has_active_break_glass
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import CLEARANCE_RANK
from app.modules.knowledge.models import KnowledgePack


def _has(user: CurrentUser, capability: str) -> bool:
    return AuthzService.can_capability(user, capability).allow


def can_manage_templates(user: CurrentUser) -> bool:
    return _has(user, "template:publish") or _has(user, "template:manage")


def _clearance_ok(pack: KnowledgePack, user: CurrentUser) -> bool:
    if pack.scope == "personal" and pack.owner_id == user.id:
        return True
    return CLEARANCE_RANK.get(pack.classification, 99) <= CLEARANCE_RANK.get(
        user.clearance, 0
    )


def can_read_template(pack: KnowledgePack, user: CurrentUser) -> bool:
    if pack.owner_status != "active" or not pack.enabled:
        return False
    if pack.scope == "system":
        return _clearance_ok(pack, user)
    if pack.tenant_id != user.tenant_id:
        return False
    if not _clearance_ok(pack, user):
        return False
    if pack.scope == "personal":
        return pack.owner_id == user.id
    if pack.scope == "tenant":
        return pack.status == "published" or can_manage_templates(user)
    return False


async def can_read_template_with_break_glass(
    session: AsyncSession, pack: KnowledgePack, user: CurrentUser
) -> bool:
    if can_read_template(pack, user):
        return True
    if (
        pack.scope != "personal"
        or pack.tenant_id != user.tenant_id
        or pack.owner_id == user.id
        or pack.owner_status != "active"
        or not pack.enabled
    ):
        return False
    return await has_active_break_glass(
        session,
        user=user,
        resource_type="knowledge_pack",
        resource_id=pack.id,
    )


def template_visibility_conditions(user: CurrentUser):
    conditions = [
        KnowledgePack.scope == "system",
        and_(
            KnowledgePack.tenant_id == user.tenant_id,
            KnowledgePack.scope == "tenant",
            KnowledgePack.status == "published",
        ),
        and_(
            KnowledgePack.tenant_id == user.tenant_id,
            KnowledgePack.scope == "personal",
            KnowledgePack.owner_id == user.id,
        ),
    ]
    if can_manage_templates(user):
        conditions.append(
            and_(
                KnowledgePack.tenant_id == user.tenant_id,
                KnowledgePack.scope == "tenant",
            )
        )
    return or_(*conditions)
