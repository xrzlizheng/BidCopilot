from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OrgUnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["department", "team"] = "department"
    parent_id: UUID | None = None
    sort_order: int = 0


class OrgUnitNode(BaseModel):
    id: UUID
    parent_id: UUID | None
    kind: str
    name: str
    path: str
    sort_order: int
    status: str
    children: list["OrgUnitNode"] = Field(default_factory=list)


class OrgMemberCreate(BaseModel):
    user_id: UUID
    member_role: Literal["member", "manager"] = "member"
    is_primary: bool = False


class OrgMemberResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    member_role: str
    is_primary: bool


OrgUnitNode.model_rebuild()
