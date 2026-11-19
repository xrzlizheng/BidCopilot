from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.assets.domain import ASSET_KINDS
from app.modules.assets.models import FieldPolicy
from app.modules.assets.schemas import FieldPolicyUpsert
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import CLEARANCE_RANK

ASSET_FIELDS = {
    "qualifications": {"name", "level", "cert_no", "issuer", "valid_from", "valid_to"},
    "personnel": {"name", "cert_type", "level", "major", "cert_no", "valid_to"},
    "performances": {"name", "project_name", "amount", "complete_date", "buyer", "category"},
    "credits": {"name", "check_item", "status", "checked_at"},
}


async def list_policies(session: AsyncSession, tenant_id: UUID) -> list[FieldPolicy]:
    return list(
        (
            await session.execute(
                select(FieldPolicy)
                .where(FieldPolicy.tenant_id == tenant_id)
                .order_by(FieldPolicy.resource_type, FieldPolicy.field_name)
            )
        )
        .scalars()
        .all()
    )


async def upsert_policy(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    body: FieldPolicyUpsert,
    user_id: UUID,
) -> FieldPolicy:
    if body.resource_type not in ASSET_KINDS:
        raise ValidationAppError("无效的资产类型")
    if body.field_name not in ASSET_FIELDS[body.resource_type]:
        raise ValidationAppError("无效的资产字段")
    row = (
        await session.execute(
            select(FieldPolicy).where(
                FieldPolicy.tenant_id == tenant_id,
                FieldPolicy.resource_type == body.resource_type,
                FieldPolicy.field_name == body.field_name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = FieldPolicy(
            tenant_id=tenant_id,
            resource_type=body.resource_type,
            field_name=body.field_name,
        )
        session.add(row)
    row.min_clearance = body.min_clearance
    row.mask_strategy = body.mask_strategy
    row.updated_by = user_id
    await session.commit()
    await session.refresh(row)
    return row


async def delete_policy(session: AsyncSession, tenant_id: UUID, policy_id: UUID) -> None:
    row = (
        await session.execute(
            select(FieldPolicy).where(
                FieldPolicy.tenant_id == tenant_id, FieldPolicy.id == policy_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    await session.delete(row)
    await session.commit()


def _mask_value(value: Any, strategy: str) -> Any:
    if strategy == "hide":
        return None
    if strategy == "partial":
        text = str(value)
        return f"****{text[-4:]}" if len(text) > 4 else "***"
    return "***"


async def apply_field_policies(
    session: AsyncSession,
    user: CurrentUser,
    kind: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    policies = list(
        (
            await session.execute(
                select(FieldPolicy).where(
                    FieldPolicy.tenant_id == user.tenant_id,
                    FieldPolicy.resource_type == kind,
                )
            )
        )
        .scalars()
        .all()
    )
    if not policies:
        return items
    user_rank = CLEARANCE_RANK.get(user.clearance, 0)
    for item in items:
        for policy in policies:
            if CLEARANCE_RANK.get(policy.min_clearance, 99) <= user_rank:
                continue
            value = item.get(policy.field_name)
            if value is None or value == "":
                continue
            item[policy.field_name] = _mask_value(value, policy.mask_strategy)
    return items
