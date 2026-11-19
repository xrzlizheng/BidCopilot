from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import has_permission
from app.core.security import verify_password
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.schemas import CurrentUser
from app.modules.org.models import OrgMember, OrgUnit


async def authenticate(session: AsyncSession, email: str, password: str) -> UUID | None:
    user = (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user.id


async def load_user_context(session: AsyncSession, user_id: UUID) -> CurrentUser | None:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.status != "active":
        return None
    tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    if tenant is None or tenant.status != "active":
        return None
    role_rows = (
        await session.execute(
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user.id,
                UserRole.tenant_id == user.tenant_id,
                or_(
                    Role.tenant_id.is_(None),
                    Role.tenant_id == user.tenant_id,
                ),
                or_(UserRole.expires_at.is_(None), UserRole.expires_at > datetime.now(UTC)),
            )
        )
    ).all()
    role_codes: list[str] = []
    permissions: list[str] = []
    org_caps: dict[UUID, set[str]] = {}
    for assignment, role in role_rows:
        if role.code not in role_codes:
            role_codes.append(role.code)
        if assignment.scope_type == "tenant":
            for permission in role.permissions:
                if permission not in permissions:
                    permissions.append(permission)
        else:
            caps = org_caps.setdefault(assignment.scope_id, set())
            caps.update(role.permissions)
    memberships = list(
        (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user.id,
                    OrgMember.tenant_id == user.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    org_unit_ids = {membership.org_unit_id for membership in memberships}
    org_unit_ids.update(org_caps)
    org_units = list(
        (
            await session.execute(
                select(OrgUnit).where(
                    OrgUnit.tenant_id == user.tenant_id,
                    OrgUnit.id.in_(org_unit_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    ancestor_ids: set[UUID] = set()
    for unit in org_units:
        for segment in unit.path.split("/"):
            if not segment:
                continue
            try:
                ancestor_ids.add(UUID(segment))
            except ValueError:
                continue
    managed_org_unit_ids = {
        membership.org_unit_id
        for membership in memberships
        if membership.member_role == "manager"
    }
    managed_org_unit_ids.update(
        org_id
        for org_id, caps in org_caps.items()
        if has_permission(list(caps), "org:manage")
    )
    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        roles=role_codes,
        permissions=permissions,
        tenant_name=tenant.name,
        token_version=user.token_version,
        clearance=user.clearance,
        org_unit_ids=tuple(org_unit_ids),
        managed_org_unit_ids=tuple(managed_org_unit_ids),
        org_unit_ancestor_ids=tuple(ancestor_ids),
        org_capabilities=tuple(
            (org_id, tuple(sorted(caps))) for org_id, caps in org_caps.items()
        ),
    )
