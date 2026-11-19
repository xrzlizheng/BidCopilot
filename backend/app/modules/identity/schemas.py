from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    email: EmailStr
    display_name: str
    roles: list[str]
    permissions: list[str]
    clearance: str = "internal"
    tenant_name: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class CapabilitiesResponse(BaseModel):
    permissions: list[str]


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    roles: list[str]
    permissions: list[str]
    tenant_name: str | None = None
    token_version: int = 0
    clearance: str = "internal"
    org_unit_ids: tuple[UUID, ...] = ()
    managed_org_unit_ids: tuple[UUID, ...] = ()
    org_unit_ancestor_ids: tuple[UUID, ...] = ()
    org_capabilities: tuple[tuple[UUID, tuple[str, ...]], ...] = ()


class TenantUserResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str
    status: str
    roles: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    role_codes: list[str] = Field(min_length=1, max_length=5)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "disabled"] | None = None
    role_codes: list[str] | None = Field(default=None, max_length=5)
    password: str | None = Field(default=None, min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class RoleOption(BaseModel):
    id: UUID | None = None
    code: str
    name: str
    permissions: list[str]
    is_system: bool = False
    scope_types: list[str] = ["tenant"]


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    permissions: list[str] = Field(min_length=1, max_length=80)
    scope_types: list[Literal["tenant", "org_unit"]] = ["tenant"]


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    permissions: list[str] | None = Field(default=None, max_length=80)
    scope_types: list[Literal["tenant", "org_unit"]] | None = None


class RoleAssignmentItem(BaseModel):
    role_code: str
    scope_type: Literal["tenant", "org_unit"] = "tenant"
    scope_id: UUID | None = None
    expires_at: datetime | None = None


class RoleAssignmentRequest(BaseModel):
    assignments: list[RoleAssignmentItem] = Field(min_length=1, max_length=20)
