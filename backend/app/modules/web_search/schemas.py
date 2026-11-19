from datetime import datetime

from pydantic import BaseModel, Field


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    count: int | None = Field(default=None, ge=1, le=10)


class WebSearchItemResponse(BaseModel):
    title: str
    url: str
    snippet: str
    published_at: str | None


class WebSearchResponse(BaseModel):
    status: str
    provider: str
    query: str
    results: list[WebSearchItemResponse] = Field(default_factory=list)
    raw_ref: str
    charged: bool
    fetched_at: datetime
