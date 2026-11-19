from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    kind: Literal["wechat", "email"]
    target: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, max_length=200)


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    target: str
    label: str | None
    enabled: bool
