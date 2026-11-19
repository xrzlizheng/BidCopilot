from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str | None
    resource_type: str | None
    resource_id: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationReadRequest(BaseModel):
    ids: list[UUID] = Field(default_factory=list)
    all: bool = False
