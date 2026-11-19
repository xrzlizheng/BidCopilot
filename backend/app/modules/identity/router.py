from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.jwt import TokenError, create_access_token, create_refresh_token, decode_token
from app.core.security import hash_password, verify_password
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity.deps import bearer, get_current_user
from app.modules.identity.models import User
from app.modules.identity.rate_limit import (
    client_ip,
    login_allowed,
    record_login_failure,
    reset_login_failures,
)
from app.modules.identity.refresh_store import consume_refresh_jti
from app.modules.identity.schemas import (
    CapabilitiesResponse,
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    UserResponse,
)
from app.modules.identity.service import authenticate, load_user_context

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.JWT_REFRESH_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV == "production",
        path=COOKIE_PATH,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    email = body.email.strip().lower()
    ip = client_ip(request)
    if not await login_allowed(ip, email):
        raise RateLimitedError("登录尝试过于频繁，请稍后再试")
    user_id = await authenticate(session, email, body.password)
    if user_id is None:
        await record_login_failure(ip, email)
        raise UnauthorizedError("邮箱或密码不正确")
    user = await load_user_context(session, user_id)
    if user is None:
        await record_login_failure(ip, email)
        raise UnauthorizedError("用户不可用")
    await reset_login_failures(email, ip)

    access_token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=user.roles,
        permissions=user.permissions,
        token_version=user.token_version,
    )
    _set_refresh_cookie(
        response,
        create_refresh_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_version=user.token_version,
        ),
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        data={"email": user.email},
    )
    await session.commit()
    return LoginResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TTL_SECONDS,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    session: AsyncSession = Depends(get_session),
) -> RefreshResponse:
    if not refresh_token:
        raise UnauthorizedError("缺少刷新令牌")
    try:
        payload = decode_token(refresh_token, "refresh")
        user_id = UUID(str(payload["sub"]))
        tenant_id = UUID(str(payload["tid"]))
    except (TokenError, KeyError, ValueError) as exc:
        raise UnauthorizedError("刷新令牌无效或已过期") from exc

    user = await load_user_context(session, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise UnauthorizedError("用户不可用")
    if int(payload.get("ver", 0)) != user.token_version:
        raise UnauthorizedError("会话已失效，请重新登录")

    jti = str(payload.get("jti") or "")
    replay_state = await consume_refresh_jti(jti, settings.JWT_REFRESH_TTL_SECONDS)
    if replay_state in {"replay", "replay_old"}:
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="auth.refresh_replay",
            resource_type="user",
            resource_id=str(user.id),
            data={"jti": jti, "grace": replay_state == "replay"},
        )
        if replay_state == "replay_old":
            db_user = await session.get(User, user.id)
            if db_user is not None:
                db_user.token_version += 1
        await session.commit()
        raise UnauthorizedError("刷新令牌已失效，请重新登录")

    _set_refresh_cookie(
        response,
        create_refresh_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_version=user.token_version,
        ),
    )
    return RefreshResponse(
        access_token=create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=user.roles,
            permissions=user.permissions,
            token_version=user.token_version,
        ),
        expires_in=settings.JWT_ACCESS_TTL_SECONDS,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> None:
    user_id: UUID | None = None
    token = refresh_token
    token_type = "refresh"
    if not token and credentials is not None:
        token = credentials.credentials
        token_type = "access"
    if token:
        try:
            payload = decode_token(token, token_type)
            user_id = UUID(str(payload["sub"]))
        except (TokenError, KeyError, ValueError):
            user_id = None
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is not None:
            user.token_version += 1
            await AuditService(session).append(
                tenant_id=user.tenant_id,
                actor_id=user.id,
                action="auth.logout",
                resource_type="user",
                resource_id=str(user.id),
                data={},
            )
            await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(
    user: CurrentUser = Depends(get_current_user),
) -> CapabilitiesResponse:
    return CapabilitiesResponse(permissions=user.permissions)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    db_user = await session.get(User, user.id)
    if db_user is None or not verify_password(body.current_password, db_user.password_hash):
        raise UnauthorizedError("当前密码不正确")
    db_user.password_hash = hash_password(body.new_password)
    db_user.token_version += 1
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="auth.password_change",
        resource_type="user",
        resource_id=str(user.id),
        data={},
    )
    await session.commit()
