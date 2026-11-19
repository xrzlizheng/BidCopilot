from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEndpoint(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """租户的 webhook 订阅端点。

    secret 不明文入库：`secret_ref` 指向 `secret_store` 里 AES-GCM 加密的记录，
    与模型密钥共用同一套 master key / DEK 包裹实现。
    """

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "url", name="uq_webhook_endpoints_tenant_url"),
        Index("ix_webhook_endpoints_tenant_enabled", "tenant_id", "enabled"),
    )

    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret_ref: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("secret_store.id", ondelete="SET NULL"), nullable=True
    )
    events: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """一次投递的结果留痕：status 原样记录，失败不降级成成功。"""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index(
            "ix_webhook_deliveries_tenant_endpoint_created",
            "tenant_id",
            "endpoint_id",
            "created_at",
        ),
    )

    endpoint_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # outbox：事件载荷持久化，进程重启后由调度器按 next_retry_at 补投。
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
