from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: str
    model: str | None
    prompt_version: str
    result_json: dict
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class EvaluationListItem(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    model: str | None
    created_at: datetime
    finished_at: datetime | None
    summary: dict
    error_code: str | None
    error_message: str | None
