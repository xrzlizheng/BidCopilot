"""p2 opportunity subscriptions and notifications

Revision ID: 0019_p2_subs_notifications
Revises: 0018_p2_project_package
Create Date: 2026-10-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019_p2_subs_notifications"
down_revision: str | None = "0018_p2_project_package"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "source_sync_runs",
        sa.Column("rows_new", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "opportunity_subscriptions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("interval_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_run_id", UUID, sa.ForeignKey("source_sync_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "name", name="uq_opportunity_subscriptions_name"),
    )
    op.create_index("ix_opportunity_subscriptions_tenant_id", "opportunity_subscriptions", ["tenant_id"])
    op.create_index("ix_opportunity_subscriptions_user_id", "opportunity_subscriptions", ["user_id"])
    op.create_index(
        "ix_opportunity_subscriptions_tenant_enabled",
        "opportunity_subscriptions",
        ["tenant_id", "enabled"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_tenant_user_created",
        "notifications",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_notifications_tenant_user_unread",
        "notifications",
        ["tenant_id", "user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_tenant_user_unread", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("opportunity_subscriptions")
    op.drop_column("source_sync_runs", "rows_new")
