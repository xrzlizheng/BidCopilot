from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    tenant_id: UUID
    target_tenant_id: UUID | None
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    data: dict
    created_at: datetime
