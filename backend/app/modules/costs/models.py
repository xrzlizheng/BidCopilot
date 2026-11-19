from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CostLedger(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        Index("ix_cost_ledger_tenant_created", "tenant_id", "created_at"),
        Index("ix_cost_ledger_tenant_pcode", "tenant_id", "pcode"),
        # 同一网关订单号只允许记一次账（order_no 可空；空值不参与唯一）。
        Index(
            "uq_cost_ledger_tenant_order",
            "tenant_id",
            "order_no",
            unique=True,
            postgresql_where=text("order_no IS NOT NULL"),
        ),
    )

    pcode: Mapped[str] = mapped_column(String(60), nullable=False)
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    order_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    query_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PaidCallQuota(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "paid_call_quotas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "period",
            "period_start",
            name="uq_paid_call_quota_period",
        ),
    )

    period: Mapped[str] = mapped_column(String(10), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
