from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt

from app.config import get_settings


class TokenError(Exception):
    pass


def _encode(payload: dict[str, Any], expires_delta: timedelta) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    roles: list[str],
    permissions: list[str],
    token_version: int = 0,
) -> str:
    settings = get_settings()
    return _encode(
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "roles": roles,
            "perms": permissions,
            "ver": token_version,
            "typ": "access",
        },
        timedelta(seconds=settings.JWT_ACCESS_TTL_SECONDS),
    )


def create_refresh_token(*, user_id: UUID, tenant_id: UUID, token_version: int = 0) -> str:
    settings = get_settings()
    return _encode(
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "ver": token_version,
            "typ": "refresh",
        },
        timedelta(seconds=settings.JWT_REFRESH_TTL_SECONDS),
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "tid", "typ", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("typ") != expected_type:
        raise TokenError("invalid token type")
    return payload
