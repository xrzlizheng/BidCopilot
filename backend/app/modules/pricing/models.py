from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PriceStrategy(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """一次报价策略计算的不可变快照。

    输入参数与计算结果分开存 JSONB，便于解释「当时为什么给这个价」：
    - inputs_json 保存租户主动提供的候选报价/人力投入/其他直接成本；
    - result_json 保存成本拆解、历史价格带、敏感性、三档建议和免责声明。
    """

    __tablename__ = "price_strategies"
    __table_args__ = (
        Index(
            "ix_price_strategies_tenant_project",
            "tenant_id",
            "project_id",
            "created_at",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    inputs_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    engine_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pricing-v1"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class PriceQuote(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """报价单不可变快照：总价取自策略档位或显式报价，明细合计必须等于总价。

    与 PriceStrategy 一样每次生成落一条新记录，保留「当时报了什么价」的审计链；
    strategy_id 指向定价依据快照，策略删除后置空但报价单本身不受影响。
    """

    __tablename__ = "price_quotes"
    __table_args__ = (
        Index(
            "ix_price_quotes_tenant_project",
            "tenant_id",
            "project_id",
            "created_at",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("price_strategies.id", ondelete="SET NULL"),
        nullable=True,
    )
    quote_number: Mapped[str] = mapped_column(String(60), nullable=False)
    project_name: Mapped[str] = mapped_column(String(400), nullable=False)
    buyer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CNY")
    amount_in_words: Mapped[str] = mapped_column(String(200), nullable=False)
    items_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_source: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
