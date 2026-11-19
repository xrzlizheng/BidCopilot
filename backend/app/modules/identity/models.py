from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="customer")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    clearance: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_code"),)

    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=lambda: ["tenant"])


class UserRole(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "scope_type",
            "scope_id",
            name="uq_user_roles_scoped",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="tenant")
    scope_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
