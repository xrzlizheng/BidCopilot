from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class OrgUnit(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "org_units"
    __table_args__ = (
        Index("ix_org_units_tenant_parent", "tenant_id", "parent_id"),
        Index("ix_org_units_tenant_path", "tenant_id", "path"),
    )

    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="department")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class OrgMember(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("org_unit_id", "user_id", name="uq_org_members_unit_user"),
        Index("ix_org_members_tenant_user", "tenant_id", "user_id"),
    )

    org_unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("org_units.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    member_role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
