from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class BidEvaluation(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "bid_evaluations"
    __table_args__ = (Index("ix_bid_evaluations_tenant_project", "tenant_id", "project_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    # 评标使用的约束集版本：补遗后评分项会随最新约束变化，保留版本号保证报告可追溯。
    constraint_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tender_constraints.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
