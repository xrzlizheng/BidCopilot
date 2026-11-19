"""evaluation constraint binding

评标记录绑定所用约束集版本，补遗后仍可追溯当次报告的评分口径。

Revision ID: 0074_eval_constraint
Revises: 0073_cost_ledger_order
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0074_eval_constraint"
down_revision: str | None = "0073_cost_ledger_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("bid_evaluations", sa.Column("constraint_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_bid_evaluations_constraint",
        "bid_evaluations",
        "tender_constraints",
        ["constraint_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
