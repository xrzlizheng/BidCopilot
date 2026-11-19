from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AiUsageLedger(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """按项目/按篇的 AI 用量台账。

    与 `cost_ledger`（外部网关计费）互补：本表记录产出侧计量（生成篇数、字数），
    用于代理机构按客户分摊成本与套餐权益校验。`status` 原样记录，失败不折算为 0。
    """

    __tablename__ = "ai_usage_ledger"
    __table_args__ = (
        Index("ix_ai_usage_tenant_project", "tenant_id", "project_id"),
        Index("ix_ai_usage_tenant_created", "tenant_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    piece_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
