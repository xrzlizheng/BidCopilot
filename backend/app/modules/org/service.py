from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.identity.models import User
from app.modules.org.models import OrgMember, OrgUnit
from app.modules.org.schemas import OrgMemberCreate, OrgUnitCreate

OrgNode = dict[str, Any]


async def create_unit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    body: OrgUnitCreate,
    user_id: UUID,
) -> OrgUnit:
    parent_path = "/"
    if body.parent_id is not None:
        parent = (
            await session.execute(
                select(OrgUnit).where(
                    OrgUnit.tenant_id == tenant_id, OrgUnit.id == body.parent_id
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise ValidationAppError("上级部门不存在")
        parent_path = parent.path
    unit = OrgUnit(
        id=uuid4(),
        tenant_id=tenant_id,
        parent_id=body.parent_id,
        kind=body.kind,
        name=body.name.strip(),
        path="",
        sort_order=body.sort_order,
        status="active",
        created_by=user_id,
    )
    unit.path = f"{parent_path}{unit.id}/"
    session.add(unit)
    await session.commit()
    await session.refresh(unit)
    return unit


async def list_units(session: AsyncSession, tenant_id: UUID) -> list[OrgUnit]:
    return list(
        (
            await session.execute(
                select(OrgUnit)
                .where(OrgUnit.tenant_id == tenant_id, OrgUnit.status == "active")
                .order_by(OrgUnit.sort_order, OrgUnit.name)
            )
        )
        .scalars()
        .all()
    )


def build_tree(units: list[OrgUnit]) -> list[OrgNode]:
    nodes: dict[UUID, OrgNode] = {
        unit.id: {
            "id": unit.id,
            "parent_id": unit.parent_id,
            "kind": unit.kind,
            "name": unit.name,
            "path": unit.path,
            "sort_order": unit.sort_order,
            "status": unit.status,
            "children": [],
        }
        for unit in units
    }
    roots: list[dict] = []
    for unit in units:
        node = nodes[unit.id]
        if unit.parent_id is not None and unit.parent_id in nodes:
            nodes[unit.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


async def add_member(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    org_unit_id: UUID,
    body: OrgMemberCreate,
) -> OrgMember:
    unit = (
        await session.execute(
            select(OrgUnit).where(OrgUnit.tenant_id == tenant_id, OrgUnit.id == org_unit_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError()
    user = await session.get(User, body.user_id)
    if user is None or user.tenant_id != tenant_id:
        raise ValidationAppError("用户不属于当前租户")
    member = (
        await session.execute(
            select(OrgMember).where(
                OrgMember.org_unit_id == org_unit_id, OrgMember.user_id == body.user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        member = OrgMember(
            tenant_id=tenant_id,
            org_unit_id=org_unit_id,
            user_id=body.user_id,
            member_role=body.member_role,
            is_primary=body.is_primary,
        )
        session.add(member)
    else:
        member.member_role = body.member_role
        member.is_primary = body.is_primary
    if body.is_primary:
        others = (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.user_id == body.user_id,
                    OrgMember.is_primary.is_(True),
                    OrgMember.org_unit_id != org_unit_id,
                )
            )
        ).scalars().all()
        for other in others:
            other.is_primary = False
    await session.commit()
    await session.refresh(member)
    return member


async def remove_member(
    session: AsyncSession, *, tenant_id: UUID, org_unit_id: UUID, user_id: UUID
) -> None:
    member = (
        await session.execute(
            select(OrgMember).where(
                OrgMember.tenant_id == tenant_id,
                OrgMember.org_unit_id == org_unit_id,
                OrgMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFoundError()
    await session.delete(member)
    await session.commit()


async def list_members(
    session: AsyncSession, tenant_id: UUID, org_unit_id: UUID
) -> list[dict]:
    unit = (
        await session.execute(
            select(OrgUnit).where(OrgUnit.tenant_id == tenant_id, OrgUnit.id == org_unit_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError()
    rows = (
        await session.execute(
            select(OrgMember, User)
            .join(User, User.id == OrgMember.user_id)
            .where(
                OrgMember.tenant_id == tenant_id,
                OrgMember.org_unit_id == org_unit_id,
            )
            .order_by(OrgMember.member_role, User.display_name)
        )
    ).all()
    return [
        {
            "user_id": member.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "member_role": member.member_role,
            "is_primary": member.is_primary,
        }
        for member, user in rows
    ]
