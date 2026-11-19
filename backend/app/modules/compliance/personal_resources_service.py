from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.identity.models import User
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.models import KnowledgeBase, KnowledgePack
from app.modules.system.models import ModelConfig, ModelSecret
from app.modules.system.secrets import revoke_secret

# 动态模型注册表：值是 ORM 类，按字符串派发；标注 Any 是因为 select(动态类)
# 的返回会被 mypy 收窄成公共基类 Base，反而丢掉真实列信息。
PERSONAL_MODELS: dict[str, Any] = {
    "knowledge_base": KnowledgeBase,
    "knowledge_pack": KnowledgePack,
}


async def orphan_personal_resources(
    session: AsyncSession, tenant_id: UUID, user_id: UUID
) -> None:
    await session.execute(
        update(KnowledgeBase)
        .where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.scope == "personal",
            KnowledgeBase.owner_id == user_id,
        )
        .values(owner_status="orphaned")
    )
    await session.execute(
        update(KnowledgePack)
        .where(
            KnowledgePack.tenant_id == tenant_id,
            KnowledgePack.scope == "personal",
            KnowledgePack.owner_id == user_id,
        )
        .values(owner_status="orphaned")
    )
    configs = (
        await session.execute(
            select(ModelConfig).where(
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.scope == "personal",
                ModelConfig.owner_id == user_id,
            )
        )
    ).scalars().all()
    for config in configs:
        config.enabled = False
        if config.secret_ref is not None:
            secret = await session.get(ModelSecret, config.secret_ref)
            if secret is not None:
                revoke_secret(secret)


async def restore_personal_resources(
    session: AsyncSession, tenant_id: UUID, user_id: UUID
) -> None:
    await session.execute(
        update(KnowledgeBase)
        .where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.scope == "personal",
            KnowledgeBase.owner_id == user_id,
            KnowledgeBase.owner_status == "orphaned",
        )
        .values(owner_status="active")
    )
    await session.execute(
        update(KnowledgePack)
        .where(
            KnowledgePack.tenant_id == tenant_id,
            KnowledgePack.scope == "personal",
            KnowledgePack.owner_id == user_id,
            KnowledgePack.owner_status == "orphaned",
        )
        .values(owner_status="active")
    )


async def transfer_personal_resource(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    resource_type: str,
    resource_id: UUID,
    target_user_id: UUID,
) -> tuple[KnowledgeBase | KnowledgePack, UUID]:
    model = PERSONAL_MODELS.get(resource_type)
    if model is None:
        raise ValidationAppError("仅支持个人知识库/模板转移")
    resource = (
        await session.execute(
            select(model).where(
                model.tenant_id == actor.tenant_id,
                model.id == resource_id,
                model.scope == "personal",
            )
        )
    ).scalar_one_or_none()
    if resource is None:
        raise NotFoundError()
    target = await session.get(User, target_user_id)
    if target is None or target.tenant_id != actor.tenant_id or target.status != "active":
        raise ValidationAppError("目标用户必须是当前租户的在职用户")
    if resource.owner_id == target.id:
        return resource, target.id
    resource.owner_id = target.id
    resource.owner_status = "active"
    if hasattr(resource, "acl_version"):
        resource.acl_version += 1
    await session.commit()
    await session.refresh(resource)
    return resource, target.id
