"""p2 harden opportunity/award fallback dedup keys

Revision ID: 0022_p2_fallback_dedup
Revises: 0021_p2_intel_synced_at
Create Date: 2026-10-24
"""
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0022_p2_fallback_dedup"
down_revision: str | None = "0021_p2_intel_synced_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _canonical_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _opportunity_fallback_key(row: Any) -> str:
    return _dedup_key(
        row["title"],
        row["buyer"] or "",
        _canonical_iso(row["publish_at"]),
        row["info_type"],
        row["project_code"] or "",
    )


def _award_fallback_key(row: Any) -> str:
    return _dedup_key(
        row["project_code"] or "",
        row["project_name"] or "",
        row["title"],
        row["winner"],
        _canonical_iso(row["award_at"]),
    )


def _apply(
    conn: Any,
    *,
    table: str,
    rows: list[Any],
    key_fn: Any,
    scope_fields: tuple[str, ...],
) -> None:
    """逐行改键并先做冲突检测；任一冲突就中止整次迁移，绝不静默合并。"""
    migrated_ids = {row["id"] for row in rows}
    taken: set[tuple[str, ...]] = set()
    for row in conn.execute(
        sa.text(f"SELECT * FROM {table}")  # noqa: S608 - table name is a fixed literal
    ).mappings():
        if row["id"] in migrated_ids:
            continue
        taken.add(tuple(str(row[field]) for field in scope_fields) + (row["dedup_key"],))

    seen: dict[tuple[str, ...], Any] = {}
    updates: list[tuple[str, Any]] = []
    for row in rows:
        new_key = key_fn(row)
        if new_key == row["dedup_key"]:
            continue
        scope = tuple(str(row[field]) for field in scope_fields) + (new_key,)
        if scope in taken:
            raise RuntimeError(
                f"{table}: recomputed key for {row['id']} collides with an existing row; "
                "resolve duplicates before migrating"
            )
        if scope in seen and seen[scope] != row["id"]:
            raise RuntimeError(
                f"{table}: rows {seen[scope]} and {row['id']} would collide after migration"
            )
        seen[scope] = row["id"]
        updates.append((new_key, row["id"]))

    for new_key, row_id in updates:
        conn.execute(
            sa.text(f"UPDATE {table} SET dedup_key = :key WHERE id = :id"),  # noqa: S608
            {"key": new_key, "id": row_id},
        )


def upgrade() -> None:
    conn = op.get_bind()
    opportunities = conn.execute(
        sa.text(
            "SELECT id, tenant_id, source, dedup_key, title, buyer, publish_at, "
            "info_type, project_code FROM opportunities WHERE source_id IS NULL"
        )
    ).mappings().all()
    _apply(
        conn,
        table="opportunities",
        rows=list(opportunities),
        key_fn=_opportunity_fallback_key,
        scope_fields=("tenant_id", "source"),
    )

    awards = conn.execute(
        sa.text(
            "SELECT a.id, a.tenant_id, a.dedup_key, a.project_code, a.project_name, "
            "a.winner, a.award_at, o.title "
            "FROM award_records a JOIN opportunities o ON o.id = a.opportunity_id "
            "WHERE o.source_id IS NULL"
        )
    ).mappings().all()
    _apply(
        conn,
        table="award_records",
        rows=list(awards),
        key_fn=_award_fallback_key,
        scope_fields=("tenant_id",),
    )


def downgrade() -> None:
    # 兜底键是有损身份：旧公式依赖原始时区写法且不含 info_type，库内字段无法可靠还原。
    # 共享环境禁止 destructive downgrade；这里保持空操作，避免制造错误身份。
    pass
