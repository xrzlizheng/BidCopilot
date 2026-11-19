from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ApiToken(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """开放 API Token。

    明文只在创建响应里返回一次；库里只留 SHA-256 哈希与用于展示的前缀，
    任何日志、列表、审计都不得出现明文。
    """

    __tablename__ = "api_tokens"
    __table_args__ = (
        Index("ix_api_tokens_tenant_created", "tenant_id", "created_at"),
        Index("ux_api_tokens_token_hash", "token_hash", unique=True),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
