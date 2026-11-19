from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_user as current_user_ctx
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.jwt import TokenError, decode_token
from app.db.session import get_session
from app.modules.access.service import AuthzService
from app.modules.identity.api_tokens import authenticate_api_token, is_api_token_value
from app.modules.identity.schemas import CurrentUser
from app.modules.identity.service import load_user_context

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedError("缺少访问令牌")
    # 开放 API Token 走独立分支：JWT 解析路径行为保持不变。
    if is_api_token_value(credentials.credentials):
        token_user = await authenticate_api_token(session, credentials.credentials)
        if token_user is None:
            raise UnauthorizedError("访问令牌无效或已过期")
        current_user_ctx.set(token_user)
        return token_user
    try:
        payload = decode_token(credentials.credentials, "access")
        user_id = UUID(str(payload["sub"]))
        tenant_id = UUID(str(payload["tid"]))
    except (TokenError, KeyError, ValueError) as exc:
        raise UnauthorizedError("访问令牌无效或已过期") from exc

    user = await load_user_context(session, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise UnauthorizedError("用户不可用")
    if int(payload.get("ver", 0)) != user.token_version:
        raise UnauthorizedError("会话已失效，请重新登录")
    current_user_ctx.set(user)
    return user


def require_permission(permission: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        AuthzService.require_capability(user, permission)
        return user

    return dependency


def require_all_permissions(
    *permissions: str,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        AuthzService.require_all_capabilities(user, permissions)
        return user

    return dependency


def require_permission_or_org(
    permission: str,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if AuthzService.can_capability(user, permission).allow:
            return user
        for org_unit_id, _ in user.org_capabilities:
            if AuthzService.can_capability_in_org(user, org_unit_id, permission).allow:
                return user
        raise ForbiddenError(f"missing permission: {permission}")

    return dependency
