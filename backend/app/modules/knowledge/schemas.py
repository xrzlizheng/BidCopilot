from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgePackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    category: str
    description: str | None
    content_json: dict[str, Any]
    version: str
    is_builtin: bool
    enabled: bool
    scope: str = "tenant"
    owner_id: UUID | None = None
    classification: str = "internal"
    status: str = "published"
    published_at: datetime | None = None


class KnowledgePackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: Literal["structure", "format", "material"]
    description: str | None = Field(default=None, max_length=1000)
    content_json: dict[str, Any]
    scope: Literal["personal", "tenant"] | None = None
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"


class KnowledgePackUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    content_json: dict[str, Any] | None = None
    enabled: bool | None = None
    status: Literal["draft", "published", "deprecated"] | None = None


class TemplateSelection(BaseModel):
    template_code: str = Field(min_length=1, max_length=100)
    format_code: str | None = Field(default=None, max_length=100)


class TemplateDraftRequest(BaseModel):
    """生成结构模板草稿的可选参数；不传 title 时由生成器根据章节自动命名。"""

    title: str | None = Field(default=None, max_length=200)


class MaterialItem(BaseModel):
    name: str
    section: str
    kind: str
    status: str
    matches: list[str] = Field(default_factory=list)
    knowledge_matches: list[str] = Field(default_factory=list)


class WorkspaceStage(BaseModel):
    key: str
    label: str
    status: str
    detail: str
    blockers: list[str] = Field(default_factory=list)


class WorkspaceResponse(BaseModel):
    project_id: UUID
    template_code: str | None
    format_code: str | None
    template_name: str | None
    format_name: str | None
    stages: list[WorkspaceStage]
    sections_total: int
    sections_approved: int
    unsourced_count: int


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scope: Literal["personal", "tenant", "org_unit"] = "personal"
    visibility: Literal["shared", "enterprise"] = "enterprise"
    org_unit_id: UUID | None = None
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: str
    owner_id: UUID | None
    org_unit_id: UUID | None = None
    name: str
    description: str | None
    classification: str
    visibility: str
    status: str
    acl_version: int
    created_at: datetime


class AclGrantRequest(BaseModel):
    subject_type: Literal["user", "tenant", "org_unit"]
    subject_id: UUID
    role: Literal["viewer", "user", "editor", "manager"]
    include_descendants: bool = False
    expires_at: datetime | None = None


class AclResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource_type: str
    resource_id: UUID
    subject_type: str
    subject_id: UUID
    role: str
    include_descendants: bool = False
    expires_at: datetime | None
    created_at: datetime


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    classification: str
    title: str
    kind: str
    filename: str
    content_type: str | None
    size_bytes: int
    status: str
    tags: list[str] = Field(default_factory=list)
    error_message: str | None = None
    text_chars: int = 0
    created_at: datetime


class KnowledgeImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    classification: str
    title: str
    caption: str | None
    tags: list[str] = Field(default_factory=list)
    filename: str
    content_type: str | None
    size_bytes: int
    width: int | None
    height: int | None
    sensitive: bool
    created_at: datetime


class KnowledgeChunkHit(BaseModel):
    document_id: UUID
    title: str
    chunk: str
    score: float


class KnowledgeImageHit(KnowledgeImageResponse):
    score: float


class KnowledgeSearchResponse(BaseModel):
    query: str
    chunks: list[KnowledgeChunkHit] = Field(default_factory=list)
    images: list[KnowledgeImageHit] = Field(default_factory=list)


class KnowledgeChunkItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_index: int
    heading: str | None
    text: str
    keywords: list[str] = Field(default_factory=list)


class KnowledgeDocumentDetail(KnowledgeDocumentResponse):
    chunks: list[KnowledgeChunkItem] = Field(default_factory=list)
    text_preview: str = ""


class KnowledgeDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: str | None = Field(default=None, min_length=1, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=20)


class KnowledgeImageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    caption: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=20)
    sensitive: bool | None = None
