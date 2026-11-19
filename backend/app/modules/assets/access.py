from typing import Any
from uuid import UUID

from sqlalchemy import false, or_, true

from app.core.permissions import has_permission
from app.modules.access.service import AuthzService
from app.modules.identity.schemas import CurrentUser


def can_manage_all_assets(user: CurrentUser) -> bool:
    return AuthzService.can_capability(user, "asset:write").allow


def can_read_all_assets(user: CurrentUser) -> bool:
    return AuthzService.can_capability(user, "asset:read").allow


def _has_org(user: CurrentUser, org_unit_id: UUID, capability: str) -> bool:
    for scope_id, capabilities in user.org_capabilities:
        if scope_id == org_unit_id and has_permission(list(capabilities), capability):
            return True
    return False


def _org_ids_with(user: CurrentUser, capability: str) -> list[UUID]:
    return [
        org_id
        for org_id, _ in user.org_capabilities
        if _has_org(user, org_id, capability)
    ]


def can_read_org(user: CurrentUser, org_unit_id: UUID | None) -> bool:
    if org_unit_id is None:
        return can_read_all_assets(user)
    if can_manage_all_assets(user):
        return True
    return _has_org(user, org_unit_id, "asset:read") or (
        can_read_all_assets(user) and org_unit_id in user.org_unit_ids
    )


def can_write_org(user: CurrentUser, org_unit_id: UUID | None) -> bool:
    if org_unit_id is None:
        return can_manage_all_assets(user)
    return (
        can_manage_all_assets(user)
        or _has_org(user, org_unit_id, "asset:write")
        or org_unit_id in user.managed_org_unit_ids
    )


def visibility_filter(user: CurrentUser, model: Any):
    if can_manage_all_assets(user):
        return true()
    conditions = []
    if can_read_all_assets(user):
        conditions.append(model.org_unit_id.is_(None))
    read_orgs = set(user.org_unit_ids) | set(_org_ids_with(user, "asset:read"))
    if read_orgs:
        conditions.append(model.org_unit_id.in_(read_orgs))
    if not conditions:
        return false()
    return or_(*conditions)
