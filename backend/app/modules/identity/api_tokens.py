"""开放 API Token 的生成、校验与吊销。

安全口径：
- 明文只在创建响应返回一次，库里只存 SHA-256 哈希（`token_hash`）与展示用前缀（`token_prefix`）。
- 认证成功后的权限 = 「token scopes ∩ 创建者当前权限」：创建者失去某个能力，
  token 立刻跟着失去，绝不放大；token 也不继承创建者的角色（避免绕过 scope 走角色 ACL）。
- 所有查询按 tenant_id 过滤；跨租户按 ID 读取一律 404。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.core.permissions import has_permission
from app.modules.identity.api_token_models import ApiToken
from app.modules.identity.schemas import CurrentUser
from app.modules.identity.service import load_user_context

TOKEN_PREFIX = "btc_"
# 32 字节 urlsafe 随机 ≈ 43 字符，加前缀共 47，远超可爆破范围。
TOKEN_RANDOM_BYTES = 32
# 展示前缀长度（含 btc_）：足够定位是哪一枚 token，又不足以反推明文。
PREFIX_DISPLAY_LENGTH = 12
MAX_SCOPES = 80
MAX_EXPIRES_DAYS = 3650


def generate_token_value() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_prefix(value: str) -> str:
    return value[:PREFIX_DISPLAY_LENGTH]


def is_api_token_value(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX)


def _validate_scopes(scopes: list[str]) -> list[str]:
    cleaned: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if not value:
            continue
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValidationAppError("scopes 不能为空")
    if len(cleaned) > MAX_SCOPES:
        raise ValidationAppError(f"scopes 最多 {MAX_SCOPES} 项")
    return cleaned


async def list_tokens(session: AsyncSession, tenant_id: UUID) -> list[ApiToken]:
    """本租户未吊销的 token；只按前缀展示，不返回、也不读取明文。"""
    return list(
        (
            await session.execute(
                select(ApiToken)
                .where(ApiToken.tenant_id == tenant_id, ApiToken.revoked_at.is_(None))
                .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_token_or_404(
    session: AsyncSession, tenant_id: UUID, token_id: UUID
) -> ApiToken:
    row = (
        await session.execute(
            select(ApiToken).where(ApiToken.tenant_id == tenant_id, ApiToken.id == token_id)
        )
    ).scalar_one_or_none()
    if row is None:
        # 跨租户与不存在同口径 404，不泄露存在性。
        raise NotFoundError()
    return row


async def create_token(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    name: str,
    scopes: list[str],
    expires_in_days: int | None = None,
) -> tuple[ApiToken, str]:
    """返回 (记录, 明文)。明文仅此一次，调用方不得落库/写日志。"""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationAppError("token 名称不能为空")
    if expires_in_days is not None and not (1 <= expires_in_days <= MAX_EXPIRES_DAYS):
        raise ValidationAppError(f"有效期必须在 1 到 {MAX_EXPIRES_DAYS} 天之间")
    plaintext = generate_token_value()
    token = ApiToken(
        tenant_id=tenant_id,
        name=cleaned_name,
        token_prefix=token_prefix(plaintext),
        token_hash=hash_token(plaintext),
        scopes=_validate_scopes(scopes),
        created_by=user_id,
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        ),
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token, plaintext


async def revoke_token(session: AsyncSession, tenant_id: UUID, token_id: UUID) -> ApiToken:
    """软吊销：保留记录以便追溯「谁在什么时候用过哪枚 token」。"""
    token = await get_token_or_404(session, tenant_id, token_id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(token)
    return token


def _effective_permissions(token: ApiToken, creator: CurrentUser) -> list[str]:
    """scopes ∩ 创建者当前权限；用 has_permission 保持通配符语义一致。"""
    return [scope for scope in token.scopes if has_permission(creator.permissions, scope)]


async def authenticate_api_token(
    session: AsyncSession, plaintext: str
) -> CurrentUser | None:
    """校验 Bearer btc_... 并更新 last_used_at。

    返回 None 表示 token 无效（不存在/已吊销/已过期/创建者不可用），
    由调用方统一转 401，避免暴露具体原因。
    """
    token = (
        await session.execute(
            select(ApiToken).where(ApiToken.token_hash == hash_token(plaintext))
        )
    ).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    if token.expires_at is not None and token.expires_at <= now:
        return None
    if token.created_by is None:
        return None
    creator = await load_user_context(session, token.created_by)
    if creator is None or creator.tenant_id != token.tenant_id:
        return None
    token.last_used_at = now
    await session.commit()
    return CurrentUser(
        id=creator.id,
        tenant_id=creator.tenant_id,
        email=creator.email,
        display_name=creator.display_name,
        # 不继承角色：token 只能拿到显式 scopes，避免角色 ACL 被顺带放大。
        roles=["api_token"],
        permissions=_effective_permissions(token, creator),
        tenant_name=creator.tenant_name,
        token_version=creator.token_version,
        # clearance 只作为额外收紧条件继承（不会放宽），组织范围一律不带。
        clearance=creator.clearance,
    )
