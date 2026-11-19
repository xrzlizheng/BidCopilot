"""回填历史补遗约束集：把「补遗只写变更项导致的最新约束集缺维度」补齐。

背景（对应 test-report-3 的 D4）：修复前上传 document_kind=amendment 时，新版本
约束集只由补遗文本解析而成，未提及的维度（评分项/资格/否决条款…）会变成空数组，
导致评分矩阵、AI 评标、逐条响应、合规预检在下游全部归零。

修复后新上传的补遗已按维度继承上一版；本脚本对**存量**行做同样补齐：
取每个项目「版本最大的 amendment 约束集」，用它的上一版（version 更小的最近一版）
补空维度，然后写回 payload。

默认 dry-run，加 --apply 才写库。写前会打印每个项目的补齐项。

用法（容器内执行）：
    docker cp scripts/backfill_amendment_constraints.py \
        bidcopilot-app:/opt/bidcopilot/scripts/
    docker exec -e DATABASE_URL=... -w /opt/bidcopilot/backend bidcopilot-app \
        python /opt/bidcopilot/scripts/backfill_amendment_constraints.py
    # 确认后加 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.main  # noqa: E402,F401  注册全部 ORM 模型
from app.db.session import SessionLocal  # noqa: E402
from app.modules.projects.models import (  # noqa: E402
    ScoringItem,
    TenderConstraint,
    TenderDocument,
)
from app.modules.projects.service import (  # noqa: E402
    AMENDMENT_INHERIT_LIST_KEYS,
    _is_blank,
)


def _scoring_rows(constraint: TenderConstraint, items: list[dict]) -> list[ScoringItem]:
    """按约束集 payload 重建 ScoringItem 行（与 service._execute_parse 口径一致）。"""
    rows: list[ScoringItem] = []
    for item in items or []:
        score = item.get("score")
        rows.append(
            ScoringItem(
                id=uuid4(),
                tenant_id=constraint.tenant_id,
                project_id=constraint.project_id,
                constraint_id=constraint.id,
                category=item.get("category") or "其他",
                item=item.get("item") or "",
                score=Decimal(str(score)) if score is not None else None,
                criteria=item.get("criteria"),
                source_span=item.get("source_span") or {},
                confidence=item.get("confidence") or "medium",
            )
        )
    return rows


async def run(args: argparse.Namespace) -> int:
    fixed = 0
    scoring_fixed = 0
    inspected = 0
    skipped = 0
    async with SessionLocal() as session:
        amendment_rows = list(
            (
                await session.execute(
                    select(TenderConstraint)
                    .join(TenderDocument, TenderDocument.id == TenderConstraint.document_id)
                    .where(TenderDocument.document_kind == "amendment")
                    .order_by(TenderConstraint.project_id, TenderConstraint.version.desc())
                )
            )
            .scalars()
            .all()
        )
        print(f"amendment 约束集共 {len(amendment_rows)} 行")

        # 每个项目只看最新的那版 amendment
        seen_projects: set = set()
        for row in amendment_rows:
            if row.project_id in seen_projects:
                continue
            seen_projects.add(row.project_id)
            inspected += 1

            previous = (
                await session.execute(
                    select(TenderConstraint)
                    .where(
                        TenderConstraint.project_id == row.project_id,
                        TenderConstraint.version < row.version,
                    )
                    .order_by(TenderConstraint.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if previous is None:
                print(f"  跳过 {row.project_id} v{row.version}：没有上一版可继承")
                skipped += 1
                continue

            payload = dict(row.payload or {})
            prev_payload = previous.payload or {}
            inherited: list[str] = []
            for key in AMENDMENT_INHERIT_LIST_KEYS:
                if _is_blank(payload.get(key)) and not _is_blank(prev_payload.get(key)):
                    payload[key] = prev_payload.get(key)
                    inherited.append(key)
            for key in ("budget", "deposit"):
                current = payload.get(key) or {}
                previous_value = prev_payload.get(key) or {}
                marker = "amount" if key == "budget" else "amount"
                if _is_blank(current.get(marker)) and not _is_blank(previous_value.get(marker)):
                    payload[key] = previous_value
                    inherited.append(key)
            if payload.get("bid_validity_days") is None and prev_payload.get("bid_validity_days") is not None:
                payload["bid_validity_days"] = prev_payload.get("bid_validity_days")
                inherited.append("bid_validity_days")

            # payload 补齐（可能上一轮已补过）与评分项表补齐分开判断：
            # 矩阵/评标按 constraint_id 读 ScoringItem 表，payload 有评分项但表里没有仍然不可用。
            existing_scoring = (
                await session.execute(
                    select(ScoringItem.id).where(ScoringItem.constraint_id == row.id)
                )
            ).scalars().all()
            payload_scoring = payload.get("scoring_items") or []
            needs_scoring_rows = not existing_scoring and bool(payload_scoring)

            if not inherited and not needs_scoring_rows:
                print(f"  无需修复 {row.project_id} v{row.version}（维度与评分项表齐全）")
                continue

            if inherited:
                meta = dict(payload.get("parse_meta") or {})
                meta["amendment_inherited_dimensions"] = sorted(set(inherited))
                meta["backfilled_from_version"] = previous.version
                payload["parse_meta"] = meta
                print(f"  补齐 {row.project_id} v{row.version} ← v{previous.version}: {sorted(set(inherited))}")
            if args.apply and inherited:
                row.payload = payload
                row.parse_meta = meta
                fixed += 1

            if needs_scoring_rows:
                print(f"    同步评分项表 +{len(payload_scoring)} 行（payload 有、表里缺）")
                if args.apply:
                    for scoring_row in _scoring_rows(row, payload_scoring):
                        session.add(scoring_row)
                    scoring_fixed += 1

        if args.apply:
            await session.commit()

    print()
    if args.apply:
        print(
            f"已处理：payload 补齐 {fixed} 个项目，评分项表补齐 {scoring_fixed} 个项目"
            f"（受检 {inspected}，跳过 {skipped}）。"
        )
    else:
        print(f"dry-run：受检 {inspected} 个项目，跳过 {skipped}，未修改数据（加 --apply 执行）。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐存量补遗约束集缺失维度")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
