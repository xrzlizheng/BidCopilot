from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity.deps import require_permission
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
from app.modules.identity.users_service import (
    create_role,
    create_user,
    delete_role,
    list_roles,
    list_users,
    set_role_assignments,
    update_role,
    update_user,
)

router = APIRouter(prefix="/api/v1", tags=["users"])


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
        resource_type="user",
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.get("/users", response_model=list[TenantUserResponse])
async def get_users(
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[TenantUserResponse]:
    return await list_users(session, user.tenant_id)


@router.get("/roles", response_model=list[RoleOption])
async def get_roles(
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[RoleOption]:
    return await list_roles(session, user.tenant_id)


@router.post("/roles", response_model=RoleOption, status_code=201)
async def post_role(
    body: RoleCreate,
    user: CurrentUser = Depends(require_permission("role:manage")),
    session: AsyncSession = Depends(get_session),
) -> RoleOption:
    try:
        created = await create_role(session, user, body)
    except ValueError as exc:
        raise ValidationAppError(str(exc)) from exc
    await _audit(
        session,
        user,
        "role.create",
        created.code,
        {"name": created.name, "permissions": created.permissions},
    )
    return created


@router.patch("/roles/{role_id}", response_model=RoleOption)
async def patch_role(
    role_id: UUID,
    body: RoleUpdate,
    user: CurrentUser = Depends(require_permission("role:manage")),
    session: AsyncSession = Depends(get_session),
) -> RoleOption:
    updated = await update_role(session, user, role_id, body)
    await _audit(
        session,
        user,
        "role.update",
        updated.code,
        {"name": updated.name, "permissions": updated.permissions},
    )
    return updated


@router.delete("/roles/{role_id}", status_code=204)
async def remove_role(
    role_id: UUID,
    user: CurrentUser = Depends(require_permission("role:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await delete_role(session, user, role_id)
    await _audit(session, user, "role.delete", str(role_id), {})


@router.post("/users", response_model=TenantUserResponse, status_code=201)
async def post_user(
    body: UserCreateRequest,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_session),
) -> TenantUserResponse:
    created = await create_user(session, user, body)
    await _audit(
        session,
        user,
        "user.create",
        str(created.id),
        {"email": created.email, "roles": created.roles},
    )
    return created


@router.put("/users/{user_id}/role-assignments", response_model=TenantUserResponse)
async def put_role_assignments(
    user_id: UUID,
    body: RoleAssignmentRequest,
    user: CurrentUser = Depends(require_permission("role:assign")),
    session: AsyncSession = Depends(get_session),
) -> TenantUserResponse:
    updated = await set_role_assignments(session, user, user_id, body)
    await _audit(
        session,
        user,
        "user.role_assignments.update",
        str(updated.id),
        {"roles": updated.roles},
    )
    return updated


@router.patch("/users/{user_id}", response_model=TenantUserResponse)
async def patch_user(
    user_id: UUID,
    body: UserUpdateRequest,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_session),
) -> TenantUserResponse:
    updated = await update_user(
        session,
        actor=user,
        user_id=user_id,
        body=body,
    )
    await _audit(
        session,
        user,
        "user.update",
        str(updated.id),
        {
            "display_name": updated.display_name,
            "status": updated.status,
            "roles": updated.roles,
            "password_changed": body.password is not None,
        },
    )
    return updated
