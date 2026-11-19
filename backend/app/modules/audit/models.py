from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_seq", "tenant_id", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    target_tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # v2 摘要是 "v2:" + HMAC-SHA256(64 hex)，共 67 字符；v1 仍为 64。
    prev_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    hash: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
