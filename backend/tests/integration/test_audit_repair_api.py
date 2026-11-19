"""审计哈希链修复集成测试：真实 PG 上模拟“清理删行断链”事故并验证受控修复。"""

from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from app.db.session import SessionLocal
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService
from tests.support import cleanup_tenants, seed_tenant_user


async def _snapshot(tenant_id: UUID) -> list[tuple]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    AuditEvent.seq,
                    AuditEvent.prev_hash,
                    AuditEvent.hash,
                    AuditEvent.action,
                    AuditEvent.data,
                )
                .where(AuditEvent.tenant_id == tenant_id)
                .order_by(AuditEvent.seq.asc())
            )
        ).all()
    return [tuple(row) for row in rows]


async def _repair_events(tenant_id: UUID) -> list[AuditEvent]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "audit.chain.repair",
                    )
                    .order_by(AuditEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.integration
async def test_repair_chain_after_pruned_rows() -> None:
    tenant_id, user_id, _email = await seed_tenant_user("audit-repair")
    try:
        async with SessionLocal() as session:
            service = AuditService(session)
            for index in range(5):
                await service.append(
                    tenant_id=tenant_id,
                    actor_id=user_id,
                    action="test.append",
                    resource_type="test",
                    resource_id=str(index),
                    data={"index": index},
                )
            await session.commit()

        async with SessionLocal() as session:
            seqs = list(
                (
                    await session.execute(
                        select(AuditEvent.seq)
                        .where(AuditEvent.tenant_id == tenant_id)
                        .order_by(AuditEvent.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert len(seqs) == 5
        deleted_seq = seqs[1]
        survivors = [seq for seq in seqs if seq != deleted_seq]
        expected_missing: list[int] = []
        for left, right in zip(survivors[:-1], survivors[1:], strict=True):
            expected_missing.extend(range(left + 1, right))
        first_bad_seq = seqs[2]

        # 模拟事故：直接 SQL 删除中间一行
        async with SessionLocal() as session:
            await session.execute(
                text("DELETE FROM audit_events WHERE tenant_id = :tenant_id AND seq = :seq"),
                {"tenant_id": tenant_id, "seq": deleted_seq},
            )
            await session.commit()

        async with SessionLocal() as session:
            service = AuditService(session)
            assert await service.verify_chain(tenant_id) is False

        # dry_run：报告断点但库未变、不写修复事件
        before = await _snapshot(tenant_id)
        async with SessionLocal() as session:
            report = await AuditService(session).repair_chain(tenant_id, dry_run=True)
        assert report == {
            "tenant_id": str(tenant_id),
            "event_count": 4,
            "first_bad_seq": first_bad_seq,
            "missing_seqs": expected_missing,
            "repaired_count": 3,
            "dry_run": True,
        }
        assert await _snapshot(tenant_id) == before
        assert await _repair_events(tenant_id) == []
        async with SessionLocal() as session:
            assert await AuditService(session).verify_chain(tenant_id) is False

        # 应用修复：链自洽 + 留痕事件自然链到重算后的尾 hash
        async with SessionLocal() as session:
            report = await AuditService(session).repair_chain(tenant_id)
        assert report["dry_run"] is False
        assert report["first_bad_seq"] == first_bad_seq
        assert report["missing_seqs"] == expected_missing
        assert report["repaired_count"] == 3
        async with SessionLocal() as session:
            assert await AuditService(session).verify_chain(tenant_id) is True

        repairs = await _repair_events(tenant_id)
        assert len(repairs) == 1
        repair = repairs[0]
        assert repair.actor_id is None
        assert repair.resource_type == "audit_chain"
        assert repair.resource_id == str(tenant_id)
        assert repair.data == {
            "from_seq": first_bad_seq,
            "to_seq": seqs[-1],
            "missing_seqs": expected_missing,
            "repaired_count": 3,
            "reason": "pruned audit rows broke prev_hash links",
        }
        async with SessionLocal() as session:
            tail = (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action != "audit.chain.repair",
                    )
                    .order_by(AuditEvent.seq.desc())
                    .limit(1)
                )
            ).scalar_one()
        assert repair.prev_hash == tail.hash
        assert repair.hash.startswith("v2:")

        # 幂等：再跑一次（含 dry_run）无断点、不改写、不新增修复事件
        after_repair = await _snapshot(tenant_id)
        async with SessionLocal() as session:
            again = await AuditService(session).repair_chain(tenant_id)
        assert again["first_bad_seq"] is None
        assert again["repaired_count"] == 0
        assert again["event_count"] == 5
        async with SessionLocal() as session:
            dry_again = await AuditService(session).repair_chain(tenant_id, dry_run=True)
        assert dry_again["first_bad_seq"] is None
        assert dry_again["repaired_count"] == 0
        assert dry_again["dry_run"] is True
        assert await _snapshot(tenant_id) == after_repair
        assert len(await _repair_events(tenant_id)) == 1
        async with SessionLocal() as session:
            assert await AuditService(session).verify_chain(tenant_id) is True
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_repair_chain_empty_tenant_is_noop() -> None:
    tenant_id, _user_id, _email = await seed_tenant_user("audit-repair-empty")
    try:
        async with SessionLocal() as session:
            report = await AuditService(session).repair_chain(tenant_id)
        assert report == {
            "tenant_id": str(tenant_id),
            "event_count": 0,
            "first_bad_seq": None,
            "missing_seqs": [],
            "repaired_count": 0,
            "dry_run": False,
        }
        async with SessionLocal() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.tenant_id == tenant_id)
                )
            ).scalar_one()
        assert count == 0
    finally:
        await cleanup_tenants([tenant_id])