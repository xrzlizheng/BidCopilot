from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.org.schemas import (
    OrgMemberCreate,
    OrgMemberResponse,
    OrgUnitCreate,
    OrgUnitNode,
)
from app.modules.org.service import (
    add_member,
    build_tree,
    create_unit,
    list_members,
    list_units,
    remove_member,
)

router = APIRouter(prefix="/api/v1/org-units", tags=["org"])


@router.get("/tree", response_model=list[OrgUnitNode])
async def get_org_tree(
    user: CurrentUser = Depends(require_permission("org:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return build_tree(await list_units(session, user.tenant_id))


@router.post("", response_model=OrgUnitNode, status_code=201)
async def post_org_unit(
    body: OrgUnitCreate,
    user: CurrentUser = Depends(require_permission("org:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    unit = await create_unit(
        session, tenant_id=user.tenant_id, body=body, user_id=user.id
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="org.unit.create",
        resource_type="org_unit",
        resource_id=str(unit.id),
        data={"name": unit.name, "parent_id": str(unit.parent_id) if unit.parent_id else None},
    )
    await session.commit()
    return build_tree([unit])[0]


@router.get("/{org_unit_id}/members", response_model=list[OrgMemberResponse])
async def get_org_members(
    org_unit_id: UUID,
    user: CurrentUser = Depends(require_permission("org:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await list_members(session, user.tenant_id, org_unit_id)


@router.post(
    "/{org_unit_id}/members",
    response_model=OrgMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_org_member(
    org_unit_id: UUID,
    body: OrgMemberCreate,
    user: CurrentUser = Depends(require_permission("org:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    member = await add_member(
        session, tenant_id=user.tenant_id, org_unit_id=org_unit_id, body=body
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="org.member.add",
        resource_type="org_unit",
        resource_id=str(org_unit_id),
        data={"user_id": str(member.user_id), "role": member.member_role},
    )
    await session.commit()
    return next(
        row
        for row in await list_members(session, user.tenant_id, org_unit_id)
        if row["user_id"] == member.user_id
    )


@router.delete("/{org_unit_id}/members/{user_id}", status_code=204)
async def delete_org_member(
    org_unit_id: UUID,
    user_id: UUID,
    user: CurrentUser = Depends(require_permission("org:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await remove_member(
        session, tenant_id=user.tenant_id, org_unit_id=org_unit_id, user_id=user_id
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="org.member.remove",
        resource_type="org_unit",
        resource_id=str(org_unit_id),
        data={"user_id": str(user_id)},
    )
    await session.commit()
