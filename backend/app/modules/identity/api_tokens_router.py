from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.identity import api_tokens
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["api-tokens"])


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=80)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    token_prefix: str
    scopes: list[str]
    created_by: UUID | None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreated(ApiTokenResponse):
    # 明文只在创建响应里出现一次；其余接口一律不返回。
    token: str


async def _audit(
    session: AsyncSession, user: CurrentUser, action: str, resource_id: str, data: dict
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type="api_token",
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.get("/api-tokens", response_model=list[ApiTokenResponse])
async def list_api_tokens(
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> list[ApiTokenResponse]:
    rows = await api_tokens.list_tokens(session, user.tenant_id)
    return [ApiTokenResponse.model_validate(row) for row in rows]


@router.post("/api-tokens", response_model=ApiTokenCreated, status_code=201)
async def create_api_token(
    body: ApiTokenCreate,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> ApiTokenCreated:
    row, plaintext = await api_tokens.create_token(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
    )
    # 审计只记前缀与 scopes，绝不含明文。
    await _audit(
        session,
        user,
        "api_token.create",
        str(row.id),
        {"name": row.name, "prefix": row.token_prefix, "scopes": list(row.scopes)},
    )
    return ApiTokenCreated(**ApiTokenResponse.model_validate(row).model_dump(), token=plaintext)


@router.delete("/api-tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: UUID,
    user: CurrentUser = Depends(require_permission("tenant:settings:manage")),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await api_tokens.revoke_token(session, user.tenant_id, token_id)
    await _audit(
        session,
        user,
        "api_token.revoke",
        str(row.id),
        {"name": row.name, "prefix": row.token_prefix},
    )
