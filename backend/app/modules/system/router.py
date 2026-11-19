from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ForbiddenError, ValidationAppError
from app.core.permissions import has_permission
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity.deps import get_current_user, require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.system.pricing_settings import (
    get_pricing_settings,
    update_pricing_settings,
)
from app.modules.system.runtime_settings import (
    get_runtime_settings,
    update_runtime_settings,
)
from app.modules.system.schemas import (
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelPolicyResponse,
    ModelPolicyUpdate,
    ModelPurpose,
    PricingSettingsUpdate,
    RuntimeSettingsUpdate,
)
from app.modules.system.service import (
    effective_model,
    get_personal_key_policy,
    list_model_configs,
    list_personal_configs,
    revoke_personal_config,
    set_personal_key_policy,
    update_model_config,
)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/decision-settings")
async def get_decision_settings(
    user: CurrentUser = Depends(require_permission("tenant:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await get_runtime_settings(session, user.tenant_id)


@router.put("/decision-settings")
async def put_decision_settings(
    body: RuntimeSettingsUpdate,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await update_runtime_settings(
            session, tenant_id=user.tenant_id, user_id=user.id, body=body
        )
    except ValueError as exc:
        raise ValidationAppError(str(exc)) from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="runtime_settings.update",
        resource_type="tenant_settings",
        resource_id="decision-settings",
        data={
            "weights": result["weights"],
            "thresholds": result["thresholds"],
            "cost_model": result["cost_model"],
            "reminders": result["reminders"],
        },
    )
    await session.commit()
    return result


@router.get("/pricing-settings")
async def get_pricing_settings_endpoint(
    user: CurrentUser = Depends(require_permission("tenant:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await get_pricing_settings(session, user.tenant_id)


@router.put("/pricing-settings")
async def put_pricing_settings_endpoint(
    body: PricingSettingsUpdate,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await update_pricing_settings(
            session, tenant_id=user.tenant_id, user_id=user.id, body=body
        )
    except ValueError as exc:
        raise ValidationAppError(str(exc)) from exc
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="pricing_settings.update",
        resource_type="tenant_settings",
        resource_id="pricing-settings",
        data={
            "cost_model": result["cost_model"],
            "target_margins": result["target_margins"],
        },
    )
    await session.commit()
    return result


def _status(effective: dict, *, reveal_host: bool = True) -> dict[str, object]:
    base_url = effective.get("base_url")
    return {
        "configured": bool(effective.get("configured")),
        "model": effective.get("model"),
        # 网关 host 属于基础设施信息，只对具备模型元数据权限的角色可见。
        "host": urlsplit(base_url).netloc if base_url and reveal_host else None,
        "overridden": bool(effective.get("overridden")),
    }


def _ocr_status(settings: object) -> dict[str, object]:
    from app.modules.tender.ocr import build_ocr_provider

    provider = build_ocr_provider(settings)
    return {
        "provider": provider.name if provider is not None else None,
        "tier": getattr(settings, "MINERU_TIER", "basic")
        if provider is not None and provider.name == "mineru"
        else None,
        "choice": getattr(settings, "OCR_PROVIDER", "auto"),
    }


@router.get("/ai-status")
async def ai_status(
    model_scope: str = Query(default="tenant", pattern="^(tenant|personal)$"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    allow_personal = model_scope == "personal"
    main = await effective_model(
        session, user.tenant_id, "main", user=user, allow_personal=allow_personal
    )
    judge = await effective_model(
        session, user.tenant_id, "judge", user=user, allow_personal=allow_personal
    )
    settings = get_settings()
    reveal_host = has_permission(user.permissions, "model:read_metadata")
    if settings.WEB_SEARCH_PROVIDER == "deepseek":
        search_base_url = settings.DEEPSEEK_SEARCH_BASE_URL
        search_configured = bool(settings.DEEPSEEK_SEARCH_API_KEY)
    else:
        search_base_url = settings.MINIMAX_SEARCH_BASE_URL
        search_configured = bool(settings.MINIMAX_SEARCH_API_KEY or settings.MINIMAX_API_KEY)
    return {
        "llm": _status(main, reveal_host=reveal_host),
        "judge": _status(judge, reveal_host=reveal_host),
        "judge_fallback_to_main": judge["model"] == main["model"],
        "web_search": {
            "configured": search_configured,
            "provider": settings.WEB_SEARCH_PROVIDER,
            "host": urlsplit(search_base_url).netloc or None,
        },
        "ocr": _ocr_status(settings),
    }


@router.get("/model-configs", response_model=list[ModelConfigResponse])
async def get_model_configs(
    user: CurrentUser = Depends(require_permission("model:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await list_model_configs(session, user.tenant_id)


@router.get("/model-policy", response_model=ModelPolicyResponse)
async def get_model_policy(
    user: CurrentUser = Depends(require_permission("model:policy:manage")),
    session: AsyncSession = Depends(get_session),
) -> ModelPolicyResponse:
    return ModelPolicyResponse(
        byok_enabled=get_settings().MODEL_BYOK_ENABLED,
        allow_personal_key_for_enterprise_data=await get_personal_key_policy(
            session, user.tenant_id
        ),
    )


@router.patch("/model-policy", response_model=ModelPolicyResponse)
async def patch_model_policy(
    body: ModelPolicyUpdate,
    user: CurrentUser = Depends(require_permission("model:policy:manage")),
    session: AsyncSession = Depends(get_session),
) -> ModelPolicyResponse:
    enabled = await set_personal_key_policy(
        session,
        tenant_id=user.tenant_id,
        enabled=body.allow_personal_key_for_enterprise_data,
        user_id=user.id,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="model_policy.update",
        resource_type="tenant",
        resource_id=str(user.tenant_id),
        data={"allow_personal_key_for_enterprise_data": enabled},
    )
    await session.commit()
    return ModelPolicyResponse(
        byok_enabled=get_settings().MODEL_BYOK_ENABLED,
        allow_personal_key_for_enterprise_data=enabled,
    )


@router.get("/model-configs/personal", response_model=list[ModelConfigResponse])
async def get_personal_model_configs(
    user: CurrentUser = Depends(require_permission("model:use")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await list_personal_configs(session, user)


@router.put("/model-configs/personal/{purpose}", response_model=ModelConfigResponse)
async def put_personal_model_config(
    purpose: ModelPurpose,
    body: ModelConfigUpdate,
    user: CurrentUser = Depends(require_permission("model:use")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not get_settings().MODEL_BYOK_ENABLED:
        raise ForbiddenError("个人模型配置未启用", code="policy_denied")
    row = await update_model_config(
        session,
        tenant_id=user.tenant_id,
        purpose=purpose,
        base_url=body.base_url,
        model=body.model,
        enabled=body.enabled,
        user_id=user.id,
        api_key=body.api_key,
        scope="personal",
        owner_id=user.id,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="model_config.personal.update",
        resource_type="model_config",
        resource_id=purpose,
        data={
            "model": body.model,
            "enabled": body.enabled,
            "key_last4": row.get("key_last4"),
        },
    )
    await session.commit()
    return row


@router.delete("/model-configs/personal/{purpose}", status_code=204)
async def delete_personal_model_config(
    purpose: ModelPurpose,
    user: CurrentUser = Depends(require_permission("model:use")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await revoke_personal_config(session, user, purpose)
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="model_config.personal.revoke",
        resource_type="model_config",
        resource_id=purpose,
        data={},
    )
    await session.commit()


@router.patch("/model-configs/{purpose}", response_model=ModelConfigResponse)
async def patch_model_config(
    purpose: ModelPurpose,
    body: ModelConfigUpdate,
    user: CurrentUser = Depends(require_permission("model:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await update_model_config(
        session,
        tenant_id=user.tenant_id,
        purpose=purpose,
        base_url=body.base_url,
        model=body.model,
        enabled=body.enabled,
        user_id=user.id,
        api_key=body.api_key,
        scope="tenant",
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="model_config.update",
        resource_type="model_config",
        resource_id=purpose,
        data={
            "model": body.model,
            "enabled": body.enabled,
            "key_last4": row.get("key_last4"),
        },
    )
    await session.commit()
    return row
