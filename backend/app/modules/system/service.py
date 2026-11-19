from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import NotFoundError, ValidationAppError
from app.modules.identity.schemas import CurrentUser
from app.modules.system.models import ModelConfig, ModelSecret, TenantSetting
from app.modules.system.secrets import decrypt_secret, encrypt_secret, revoke_secret
from app.modules.system.url_policy import validate_model_base_url

PURPOSES = {"main", "judge"}
MODEL_POLICY_KEY = "model.allow_personal_key_for_enterprise_data"


async def get_personal_key_policy(session: AsyncSession, tenant_id: UUID) -> bool:
    row = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == MODEL_POLICY_KEY,
            )
        )
    ).scalar_one_or_none()
    return bool(row.value.get("enabled")) if row is not None else False


async def set_personal_key_policy(
    session: AsyncSession, *, tenant_id: UUID, enabled: bool, user_id: UUID
) -> bool:
    row = (
        await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == MODEL_POLICY_KEY,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TenantSetting(tenant_id=tenant_id, key=MODEL_POLICY_KEY, value={})
        session.add(row)
    row.value = {"enabled": enabled}
    row.updated_by = user_id
    await session.commit()
    return enabled


def _defaults(purpose: str) -> dict[str, Any]:
    settings = get_settings()
    if purpose == "main":
        return {
            "base_url": settings.LLM_BASE_URL,
            "model": settings.LLM_MODEL,
            "api_key": settings.LLM_API_KEY,
            "timeout_seconds": settings.LLM_TIMEOUT_SECONDS,
        }
    return {
        "base_url": settings.JUDGE_BASE_URL or settings.LLM_BASE_URL,
        "model": settings.JUDGE_MODEL or settings.LLM_MODEL,
        "api_key": settings.JUDGE_API_KEY or settings.LLM_API_KEY,
        "timeout_seconds": settings.JUDGE_TIMEOUT_SECONDS,
    }


async def get_config(
    session: AsyncSession,
    tenant_id: UUID,
    purpose: str,
    *,
    scope: str = "tenant",
    owner_id: UUID | None = None,
) -> ModelConfig | None:
    statement = select(ModelConfig).where(
        ModelConfig.tenant_id == tenant_id,
        ModelConfig.purpose == purpose,
        ModelConfig.scope == scope,
    )
    if scope == "personal":
        statement = statement.where(ModelConfig.owner_id == owner_id)
    return (await session.execute(statement)).scalar_one_or_none()


async def _secret(session: AsyncSession, secret_ref: UUID | None) -> ModelSecret | None:
    if secret_ref is None:
        return None
    return await session.get(ModelSecret, secret_ref)


async def effective_model(
    session: AsyncSession,
    tenant_id: UUID,
    purpose: str,
    *,
    user: CurrentUser | None = None,
    allow_personal: bool = False,
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ValidationAppError("模型用途必须是 main 或 judge")
    settings = get_settings()
    defaults = _defaults(purpose)
    tenant_config = await get_config(session, tenant_id, purpose, scope="tenant")

    personal_config = None
    if (
        user is not None
        and settings.MODEL_BYOK_ENABLED
        and allow_personal
    ):
        personal_config = await get_config(
            session,
            tenant_id,
            purpose,
            scope="personal",
            owner_id=user.id,
        )

    config = personal_config if personal_config and personal_config.enabled else None
    is_personal = config is not None and personal_config is not None and config.id == personal_config.id
    credential_scope = "personal" if is_personal else "tenant"
    billing_owner_type: str | None = "user" if is_personal else "tenant"
    billing_owner_id: UUID | None = user.id if is_personal and user else tenant_id

    if config is None and tenant_config is not None and tenant_config.enabled:
        config = tenant_config
        credential_scope = "tenant"
        billing_owner_type = "tenant"
        billing_owner_id = tenant_id

    base_url = defaults["base_url"]
    model = defaults["model"]
    api_key = defaults["api_key"]
    overridden = config is not None and config.enabled
    key_last4 = None
    config_id = None
    if config is not None and config.enabled:
        base_url = config.base_url or base_url
        model = config.model or model
        config_id = config.id
        key_last4 = config.key_last4
        secret = await _secret(session, config.secret_ref)
        if secret is not None:
            api_key = decrypt_secret(secret) or api_key
    else:
        config = None
        credential_scope = "platform"
        billing_owner_type = None
        billing_owner_id = None

    return {
        "purpose": purpose,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout_seconds": defaults["timeout_seconds"],
        "overridden": overridden,
        "configured": bool(base_url and api_key and model),
        "credential_scope": credential_scope,
        "billing_owner_type": billing_owner_type,
        "billing_owner_id": billing_owner_id,
        "config_id": config_id,
        "key_last4": key_last4,
    }


async def list_model_configs(session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = []
    for purpose in ("main", "judge"):
        effective = await effective_model(session, tenant_id, purpose)
        config = await get_config(session, tenant_id, purpose, scope="tenant")
        rows.append(
            {
                "purpose": purpose,
                "scope": "tenant",
                "provider": config.provider if config else "openai_compatible",
                "base_url": config.base_url if config else None,
                "model": config.model if config else None,
                "enabled": config.enabled if config else False,
                "effective_base_url": effective["base_url"],
                "effective_model": effective["model"],
                "configured": effective["configured"],
                "overridden": effective["overridden"],
                "key_last4": config.key_last4 if config else None,
                "credential_scope": effective["credential_scope"],
            }
        )
    return rows


async def list_personal_configs(
    session: AsyncSession, user: CurrentUser
) -> list[dict[str, Any]]:
    rows = []
    for purpose in ("main", "judge"):
        config = await get_config(
            session, user.tenant_id, purpose, scope="personal", owner_id=user.id
        )
        secret = await _secret(session, config.secret_ref) if config else None
        configured = bool(
            config
            and config.enabled
            and config.base_url
            and config.model
            and secret is not None
            and secret.revoked_at is None
        )
        rows.append(
            {
                "purpose": purpose,
                "scope": "personal",
                "provider": config.provider if config else "openai_compatible",
                "base_url": config.base_url if config else None,
                "model": config.model if config else None,
                "enabled": config.enabled if config else False,
                "effective_base_url": config.base_url if config else None,
                "effective_model": config.model if config else None,
                "configured": configured,
                "overridden": bool(config and config.enabled),
                "key_last4": config.key_last4 if config else None,
                "credential_scope": "personal",
            }
        )
    return rows


async def update_model_config(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    purpose: str,
    base_url: str | None,
    model: str | None,
    enabled: bool,
    user_id: UUID,
    api_key: str | None = None,
    scope: str = "tenant",
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ValidationAppError("模型用途必须是 main 或 judge")
    if base_url:
        await validate_model_base_url(base_url)
    config = await get_config(
        session, tenant_id, purpose, scope=scope, owner_id=owner_id
    )
    if config is None:
        config = ModelConfig(
            tenant_id=tenant_id,
            purpose=purpose,
            scope=scope,
            owner_id=owner_id if scope == "personal" else None,
            provider="openai_compatible",
        )
        session.add(config)
    config.provider = config.provider or "openai_compatible"
    config.base_url = (base_url or "").strip() or None
    config.model = (model or "").strip() or None
    config.enabled = enabled
    config.updated_by = user_id
    if api_key:
        if config.secret_ref is not None:
            previous = await _secret(session, config.secret_ref)
            if previous is not None:
                revoke_secret(previous)
        secret = encrypt_secret(
            tenant_id=tenant_id,
            scope=scope,
            owner_id=owner_id if scope == "personal" else None,
            provider=config.provider,
            plaintext=api_key,
            created_by=user_id,
        )
        session.add(secret)
        await session.flush()
        config.secret_ref = secret.id
        config.key_last4 = secret.last4
    if scope == "personal" and enabled and config.secret_ref is None:
        raise ValidationAppError("启用个人模型前必须写入 API Key")
    await session.commit()
    if scope == "personal":
        if owner_id is None:
            raise ValidationAppError("个人模型配置缺少 owner")
        user = CurrentUser(
            id=owner_id,
            tenant_id=tenant_id,
            email="",
            display_name="",
            roles=[],
            permissions=[],
        )
        return next(
            row
            for row in await list_personal_configs(session, user)
            if row["purpose"] == purpose
        )
    return next(
        row for row in await list_model_configs(session, tenant_id) if row["purpose"] == purpose
    )


async def revoke_personal_config(
    session: AsyncSession, user: CurrentUser, purpose: str
) -> None:
    if purpose not in PURPOSES:
        raise ValidationAppError("模型用途必须是 main 或 judge")
    config = await get_config(
        session, user.tenant_id, purpose, scope="personal", owner_id=user.id
    )
    if config is None:
        raise NotFoundError()
    config.enabled = False
    if config.secret_ref is not None:
        secret = await _secret(session, config.secret_ref)
        if secret is not None:
            revoke_secret(secret)
    await session.commit()
