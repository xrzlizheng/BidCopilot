from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.permissions import has_permission
from app.core.security import hash_password
from app.modules.access.capabilities import validate_permissions
from app.modules.compliance.personal_resources_service import (
    orphan_personal_resources,
    restore_personal_resources,
)
from app.modules.identity.models import Role, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.identity.schemas import (
    CurrentUser,
    RoleAssignmentRequest,
    RoleCreate,
    RoleOption,
    RoleUpdate,
    TenantUserResponse,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.modules.org.models import OrgUnit

ASSIGNABLE_ROLE_CODES = [code for code in SYSTEM_ROLES if code != "platform_admin"]


async def _ensure_system_roles(session: AsyncSession) -> dict[str, Role]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('system_roles'))"))
    roles: dict[str, Role] = {}
    for code in ASSIGNABLE_ROLE_CODES:
        spec = SYSTEM_ROLES[code]
        role = (
            await session.execute(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=None,
                code=code,
                name=spec["name"],
                permissions=list(spec["permissions"]),
                is_system=True,
                scope_types=["tenant"],
            )
            session.add(role)
            await session.flush()
        else:
            role.name = spec["name"]
            role.permissions = list(spec["permissions"])
            role.scope_types = ["tenant"]
        roles[code] = role
    return roles


async def _assignable_roles(
    session: AsyncSession, tenant_id: UUID, *, create_missing: bool = False
) -> dict[str, Role]:
    if create_missing:
        roles = await _ensure_system_roles(session)
    else:
        rows = (
            await session.execute(
                select(Role).where(
                    (Role.tenant_id.is_(None)) | (Role.tenant_id == tenant_id)
                )
            )
        ).scalars().all()
        roles = {role.code: role for role in rows}
    custom = (
        await session.execute(select(Role).where(Role.tenant_id == tenant_id))
    ).scalars().all()
    for role in custom:
        roles[role.code] = role
    return roles


def _normalize_roles(codes: list[str], roles: dict[str, Role]) -> list[str]:
    normalized: list[str] = []
    for code in codes:
        if code not in roles:
            raise ValidationAppError(f"role is not assignable: {code}")
        if code not in normalized:
            normalized.append(code)
    if not normalized:
        raise ValidationAppError("至少需要分配一个角色")
    return normalized


def _ensure_role_ceiling(
    actor: CurrentUser, codes: list[str], roles: dict[str, Role]
) -> None:
    """分配的每个角色都不得超出操作者自身权限（与角色分配端点同一口径）。"""
    for code in codes:
        role = roles[code]
        for permission in role.permissions:
            if not has_permission(actor.permissions, permission):
                raise ForbiddenError(f"不能分配超出自身能力的角色: {code}")


async def _role_map(session: AsyncSession, tenant_id: UUID) -> dict[UUID, list[str]]:
    rows = (
        await session.execute(
            select(UserRole.user_id, Role.code)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.tenant_id == tenant_id)
            .order_by(Role.code)
        )
    ).all()
    result: dict[UUID, list[str]] = {}
    for user_id, code in rows:
        result.setdefault(user_id, []).append(code)
    return result


async def _active_admin_count(session: AsyncSession, tenant_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count(func.distinct(User.id)))
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.tenant_id == tenant_id,
                User.status == "active",
                Role.code == "tenant_admin",
            )
        )
    ).scalar_one()


async def list_roles(session: AsyncSession, tenant_id: UUID) -> list[RoleOption]:
    roles = await _ensure_system_roles(session)
    await session.commit()
    custom = list(
        (
            await session.execute(
                select(Role)
                .where(Role.tenant_id == tenant_id)
                .order_by(Role.name)
            )
        )
        .scalars()
        .all()
    )
    options = [
        RoleOption(
            id=roles[code].id if code in roles else None,
            code=code,
            name=str(SYSTEM_ROLES[code]["name"]),
            permissions=list(SYSTEM_ROLES[code]["permissions"]),
            is_system=True,
            scope_types=["tenant"],
        )
        for code in ASSIGNABLE_ROLE_CODES
    ]
    options.extend(
        RoleOption(
            id=role.id,
            code=role.code,
            name=role.name,
            permissions=list(role.permissions),
            is_system=False,
            scope_types=list(role.scope_types),
        )
        for role in custom
    )
    return options


def _validate_role_permissions(actor: CurrentUser, permissions: list[str]) -> None:
    validate_permissions(permissions)
    for permission in permissions:
        if permission == "*" or permission.startswith("platform:"):
            raise ValidationAppError("自定义角色不能包含平台权限")
        if not has_permission(actor.permissions, permission):
            raise ForbiddenError(f"不能授予超出自身能力的权限: {permission}")


async def create_role(
    session: AsyncSession, actor: CurrentUser, body: RoleCreate
) -> RoleOption:
    _validate_role_permissions(actor, body.permissions)
    scope_types = list(dict.fromkeys(body.scope_types or ["tenant"]))
    if not scope_types or any(scope not in {"tenant", "org_unit"} for scope in scope_types):
        raise ValidationAppError("无效的角色作用域")
    role = Role(
        tenant_id=actor.tenant_id,
        code=f"custom.{uuid4().hex[:10]}",
        name=body.name.strip(),
        permissions=list(dict.fromkeys(body.permissions)),
        is_system=False,
        scope_types=scope_types,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return RoleOption(
        id=role.id,
        code=role.code,
        name=role.name,
        permissions=list(role.permissions),
        is_system=False,
        scope_types=list(role.scope_types),
    )


async def update_role(
    session: AsyncSession,
    actor: CurrentUser,
    role_id: UUID,
    body: RoleUpdate,
) -> RoleOption:
    role = (
        await session.execute(
            select(Role).where(
                Role.id == role_id,
                Role.tenant_id == actor.tenant_id,
                Role.is_system.is_(False),
            )
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError()
    if body.permissions is not None:
        _validate_role_permissions(actor, body.permissions)
        role.permissions = list(dict.fromkeys(body.permissions))
    if body.name is not None:
        role.name = body.name.strip()
    if body.scope_types is not None:
        # 显式标注 list[str]：dict.fromkeys 会保留 Literal 元素类型，
        # 而 SQLAlchemy 列属性按 list[str] 接收，Literal 列表因不变性不兼容。
        scope_types: list[str] = list(dict.fromkeys(body.scope_types))
        if not scope_types or any(scope not in {"tenant", "org_unit"} for scope in scope_types):
            raise ValidationAppError("无效的角色作用域")
        role.scope_types = scope_types
    await session.commit()
    await session.refresh(role)
    return RoleOption(
        id=role.id,
        code=role.code,
        name=role.name,
        permissions=list(role.permissions),
        is_system=False,
        scope_types=list(role.scope_types),
    )


async def set_role_assignments(
    session: AsyncSession,
    actor: CurrentUser,
    user_id: UUID,
    body: RoleAssignmentRequest,
) -> TenantUserResponse:
    user = (
        await session.execute(
            select(User).where(
                User.tenant_id == actor.tenant_id, User.id == user_id
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError()
    roles = await _assignable_roles(session, actor.tenant_id, create_missing=True)
    current = (
        await session.execute(
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id)
        )
    ).all()
    had_tenant_admin = any(
        role.code == "tenant_admin" and assignment.scope_type == "tenant"
        for assignment, role in current
    )
    new_tenant_admin = False
    resolved: list[tuple[Role, str, UUID, object]] = []
    for item in body.assignments:
        role = roles.get(item.role_code)
        if role is None:
            raise ValidationAppError(f"role is not assignable: {item.role_code}")
        if item.scope_type not in role.scope_types:
            raise ValidationAppError(f"role {role.code} 不支持 {item.scope_type} 作用域")
        for permission in role.permissions:
            if not has_permission(actor.permissions, permission):
                raise ForbiddenError(f"不能分配超出自身能力的角色: {role.code}")
        if item.scope_type == "tenant":
            scope_id = actor.tenant_id
            if role.code == "tenant_admin":
                new_tenant_admin = True
        else:
            if item.scope_id is None:
                raise ValidationAppError("部门作用域缺少 scope_id")
            unit = await session.get(OrgUnit, item.scope_id)
            if unit is None or unit.tenant_id != actor.tenant_id:
                raise ValidationAppError("部门不存在")
            if not (
                has_permission(actor.permissions, "org:manage")
                or item.scope_id in actor.managed_org_unit_ids
            ):
                raise ForbiddenError("无权在该部门分配角色")
            scope_id = item.scope_id
        resolved.append((role, item.scope_type, scope_id, item.expires_at))
    if (
        had_tenant_admin
        and not new_tenant_admin
        and await _active_admin_count(session, actor.tenant_id) <= 1
    ):
        raise ValidationAppError("不能移除最后一个在职的租户管理员")
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for role, scope_type, scope_id, expires_at in resolved:
        session.add(
            UserRole(
                tenant_id=actor.tenant_id,
                user_id=user.id,
                role_id=role.id,
                scope_type=scope_type,
                scope_id=scope_id,
                expires_at=expires_at,
            )
        )
    user.token_version += 1
    await session.commit()
    role_map = await _role_map(session, actor.tenant_id)
    await session.refresh(user)
    return TenantUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        roles=role_map.get(user.id, []),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def delete_role(session: AsyncSession, actor: CurrentUser, role_id: UUID) -> None:
    role = (
        await session.execute(
            select(Role).where(
                Role.id == role_id,
                Role.tenant_id == actor.tenant_id,
                Role.is_system.is_(False),
            )
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError()
    assigned = (
        await session.execute(
            select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
        )
    ).scalar_one()
    if assigned:
        raise ConflictError("角色仍分配给用户，不能删除")
    await session.delete(role)
    await session.commit()


async def list_users(session: AsyncSession, tenant_id: UUID) -> list[TenantUserResponse]:
    users = list(
        (
            await session.execute(
                select(User).where(User.tenant_id == tenant_id).order_by(User.created_at)
            )
        )
        .scalars()
        .all()
    )
    role_map = await _role_map(session, tenant_id)
    return [
        TenantUserResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            roles=role_map.get(user.id, []),
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )
        for user in users
    ]


async def create_user(
    session: AsyncSession, actor: CurrentUser, body: UserCreateRequest
) -> TenantUserResponse:
    from app.modules.quota.policy import enforce_seat_limit

    tenant_id = actor.tenant_id
    await enforce_seat_limit(session, tenant_id)
    roles = await _assignable_roles(session, tenant_id, create_missing=True)
    role_codes = _normalize_roles(body.role_codes, roles)
    _ensure_role_ceiling(actor, role_codes, roles)
    email = body.email.strip().lower()
    existing = (
        await session.execute(select(User.id).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("邮箱已被使用")
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        status="active",
    )
    session.add(user)
    await session.flush()
    for code in role_codes:
        session.add(
            UserRole(
                tenant_id=tenant_id,
                user_id=user.id,
                role_id=roles[code].id,
                scope_type="tenant",
                scope_id=tenant_id,
            )
        )
    await session.commit()
    await session.refresh(user)
    return TenantUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        roles=role_codes,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def update_user(
    session: AsyncSession,
    actor: CurrentUser,
    user_id: UUID,
    body: UserUpdateRequest,
) -> TenantUserResponse:
    tenant_id = actor.tenant_id
    user = (
        await session.execute(select(User).where(User.tenant_id == tenant_id, User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError()
    roles = await _assignable_roles(session, tenant_id, create_missing=True)
    current_codes = set((await _role_map(session, tenant_id)).get(user.id, []))
    new_codes = _normalize_roles(body.role_codes, roles) if body.role_codes is not None else None
    if new_codes is not None:
        _ensure_role_ceiling(actor, new_codes, roles)
    if body.password is not None:
        target_roles = [roles[code] for code in current_codes if code in roles]
        if any(
            not has_permission(actor.permissions, permission)
            for role in target_roles
            for permission in role.permissions
        ):
            raise ForbiddenError("不能重置权限高于自身的用户口令")

    is_self = actor.id == user.id
    if is_self and body.status == "disabled":
        raise ValidationAppError("不能停用自己的账号")
    if is_self and new_codes is not None and set(new_codes) != current_codes:
        raise ValidationAppError("不能修改自己的角色")
    admin_removed = "tenant_admin" in current_codes and (
        (new_codes is not None and "tenant_admin" not in new_codes) or body.status == "disabled"
    )
    if admin_removed and await _active_admin_count(session, tenant_id) <= 1:
        raise ValidationAppError("不能停用最后一个在职的租户管理员")

    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    security_changed = body.password is not None
    if body.status is not None and body.status != user.status:
        user.status = body.status
        security_changed = True
        if body.status == "disabled":
            await orphan_personal_resources(session, tenant_id, user.id)
        elif body.status == "active":
            from app.modules.quota.policy import enforce_seat_limit

            await enforce_seat_limit(session, tenant_id)
            await restore_personal_resources(session, tenant_id, user.id)
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if new_codes is not None:
        if set(new_codes) != current_codes:
            security_changed = True
        await session.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id, UserRole.scope_type == "tenant"
            )
        )
        for code in new_codes:
            session.add(
                UserRole(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role_id=roles[code].id,
                    scope_type="tenant",
                    scope_id=tenant_id,
                )
            )
    if security_changed:
        user.token_version += 1
    await session.commit()
    role_map = await _role_map(session, tenant_id)
    await session.refresh(user)
    return TenantUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        roles=role_map.get(user.id, []),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )
