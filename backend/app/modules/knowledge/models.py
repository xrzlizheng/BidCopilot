from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeBase(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index("ix_knowledge_bases_tenant_scope", "tenant_id", "scope"),
        Index("ix_knowledge_bases_tenant_owner", "tenant_id", "owner_id"),
        Index("ix_knowledge_bases_tenant_org_unit", "tenant_id", "org_unit_id"),
    )

    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="tenant")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    org_unit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="enterprise")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    acl_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class KnowledgePack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_packs"
    __table_args__ = (
        UniqueConstraint("code", name="uq_knowledge_packs_code"),
        Index("ix_knowledge_packs_tenant_category", "tenant_id", "category"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="tenant")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    owner_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    published_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_tenant_created", "tenant_id", "created_at"),
        Index("ix_knowledge_documents_tenant_hash", "tenant_id", "file_hash"),
        Index("ix_knowledge_documents_tenant_kb", "tenant_id", "kb_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kb_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    acl_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    company_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="parsed")
    text_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_tenant_document", "tenant_id", "document_id"),
        Index("ix_knowledge_chunks_tenant_kb", "tenant_id", "kb_id"),
        Index("ix_knowledge_chunks_keywords", "keywords", postgresql_using="gin"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kb_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class KnowledgeImage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_images"
    __table_args__ = (
        Index("ix_knowledge_images_tenant_created", "tenant_id", "created_at"),
        Index("ix_knowledge_images_tenant_kb", "tenant_id", "kb_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kb_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    company_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
