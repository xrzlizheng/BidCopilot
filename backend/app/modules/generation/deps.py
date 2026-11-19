from typing import Literal

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ForbiddenError
from app.db.session import get_session
from app.modules.generation.llm_client import LLMClient
from app.modules.identity.deps import get_current_user
from app.modules.identity.schemas import CurrentUser
from app.modules.system.service import (
    effective_model,
    get_personal_key_policy,
)
from app.modules.system.url_policy import validate_model_base_url


async def get_llm_client(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
    model_scope: Literal["tenant", "personal"] = Query(default="tenant"),
) -> LLMClient:
    if model_scope == "personal" and (
        not get_settings().MODEL_BYOK_ENABLED
        or not await get_personal_key_policy(session, user.tenant_id)
    ):
        raise ForbiddenError("个人模型未启用", code="policy_denied")
    effective = await effective_model(
        session,
        user.tenant_id,
        "main",
        user=user,
        allow_personal=model_scope == "personal",
    )
    if effective.get("config_id") is not None and effective.get("base_url"):
        await validate_model_base_url(str(effective["base_url"]))
    return LLMClient(
        base_url=effective["base_url"],
        api_key=effective["api_key"],
        model=effective["model"],
        timeout_seconds=effective["timeout_seconds"],
    )


async def get_judge_client(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
    model_scope: Literal["tenant", "personal"] = Query(default="tenant"),
) -> LLMClient:
    if model_scope == "personal" and (
        not get_settings().MODEL_BYOK_ENABLED
        or not await get_personal_key_policy(session, user.tenant_id)
    ):
        raise ForbiddenError("个人模型未启用", code="policy_denied")
    effective = await effective_model(
        session,
        user.tenant_id,
        "judge",
        user=user,
        allow_personal=model_scope == "personal",
    )
    if effective.get("config_id") is not None and effective.get("base_url"):
        await validate_model_base_url(str(effective["base_url"]))
    return LLMClient(
        base_url=effective["base_url"],
        api_key=effective["api_key"],
        model=effective["model"],
        timeout_seconds=effective["timeout_seconds"],
    )
