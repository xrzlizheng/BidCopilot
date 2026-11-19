import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import AppError
from app.modules.audit.models import AuditEvent


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _expected_hash(previous_hash: str, payload: dict[str, Any], stored_hash: str, key: str) -> str:
    """verify/repair 共用的摘要与 scheme 选择：stored hash 带 "v2:" 前缀走
    HMAC-SHA256(key)，否则按历史 v1 裸 sha256。重算时 scheme 跟随原 stored hash。"""
    body = (previous_hash + canonical_json(payload)).encode("utf-8")
    if stored_hash.startswith("v2:"):
        return "v2:" + hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hashlib.sha256(body).hexdigest()


def _digest(previous_hash: str, payload: dict[str, Any]) -> str:
    """v2 审计摘要：带服务端密钥的 HMAC，持库写权限者无法整链重算。"""
    return _expected_hash(previous_hash, payload, "v2:", get_settings().JWT_SECRET_KEY)


def _event_payload(event: AuditEvent) -> dict[str, Any]:
    """与 append/verify_chain 完全一致的 canonical payload 构造。"""
    return {
        "tenant_id": str(event.tenant_id),
        "target_tenant_id": str(event.target_tenant_id) if event.target_tenant_id else None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "data": event.data,
        "created_at": event.created_at.isoformat(),
    }


def recompute_chain(
    events: Sequence[Mapping[str, Any]], key: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """纯函数修复核心（无 DB 依赖，可单元测）。

    events 为按 seq 升序的 {seq, payload, prev_hash, hash} 序列，payload 与
    verify_chain 的 canonical payload 同构；key 为 HMAC 密钥。
    返回 (updates, report)：
    - updates：自第一个 `prev_hash != previous or hash != expected` 断点起，
      逐条按序重算后实际需要改写的行 [{seq, prev_hash, hash}]；scheme 跟随
      该行 stored hash 的 "v2:" 前缀（v1 保持 v1，v2 保持 v2）。
    - report：{event_count, first_bad_seq, missing_seqs, repaired_count}；
      missing_seqs 列出相邻 seq 间隙中缺失的全部 seq 号（间隙本身不构成断点）；
      repaired_count 为实际改写行数。无断点时 first_bad_seq=None、updates 为空，
      重复调用幂等。
    """
    previous_hash = ""
    previous_seq: int | None = None
    first_bad_seq: int | None = None
    missing_seqs: list[int] = []
    updates: list[dict[str, Any]] = []
    for event in events:
        seq = int(event["seq"])
        if previous_seq is not None:
            missing_seqs.extend(range(previous_seq + 1, seq))
        expected = _expected_hash(previous_hash, event["payload"], event["hash"], key)
        if first_bad_seq is None and (
            event["prev_hash"] != previous_hash or event["hash"] != expected
        ):
            first_bad_seq = seq
        if first_bad_seq is not None:
            if event["prev_hash"] != previous_hash or event["hash"] != expected:
                updates.append({"seq": seq, "prev_hash": previous_hash, "hash": expected})
            previous_hash = expected
        else:
            previous_hash = event["hash"]
        previous_seq = seq
    report = {
        "event_count": len(events),
        "first_bad_seq": first_bad_seq,
        "missing_seqs": missing_seqs,
        "repaired_count": len(updates),
    }
    return updates, report


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        data: dict[str, Any] | None = None,
        target_tenant_id: UUID | None = None,
    ) -> AuditEvent:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:tenant_key))"),
            {"tenant_key": str(tenant_id)},
        )
        previous = (
            await self.session.execute(
                select(AuditEvent)
                .where(AuditEvent.tenant_id == tenant_id)
                .order_by(AuditEvent.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        prev_hash = previous.hash if previous is not None else ""
        created_at = datetime.now(UTC)
        payload = {
            "tenant_id": str(tenant_id),
            "target_tenant_id": str(target_tenant_id) if target_tenant_id else None,
            "actor_id": str(actor_id) if actor_id else None,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "data": data or {},
            "created_at": created_at.isoformat(),
        }
        digest = _digest(prev_hash, payload)
        event = AuditEvent(
            tenant_id=tenant_id,
            target_tenant_id=target_tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            data=data or {},
            prev_hash=prev_hash,
            hash=digest,
            created_at=created_at,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def verify_chain(self, tenant_id: UUID) -> bool:
        events = await self.session.stream_scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.asc())
            .execution_options(yield_per=500)
        )
        key = get_settings().JWT_SECRET_KEY
        previous_hash = ""
        async for event in events:
            expected = _expected_hash(previous_hash, _event_payload(event), event.hash, key)
            if event.prev_hash != previous_hash or event.hash != expected:
                return False
            previous_hash = event.hash
        return True

    async def repair_chain(self, tenant_id: UUID, *, dry_run: bool = False) -> dict[str, Any]:
        """受控修复：从该租户第一个不一致事件起按序重算 prev_hash/hash，使链自洽。

        - dry_run=True 只报告不写；无断点时 first_bad_seq=None、repaired_count=0，
          同样不写修复事件。
        - 应用修复后 append 一条 action="audit.chain.repair" 审计事件留痕，
          自然链到重算后的尾 hash；提交前用 verify_chain 自检，不通过则回滚并抛错。
        - 与 append 相同的租户 advisory lock 串行化，避免修复期间并发追加。
        """
        if not dry_run:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:tenant_key))"),
                {"tenant_key": str(tenant_id)},
            )
        events = (
            (
                await self.session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.tenant_id == tenant_id)
                    .order_by(AuditEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        records = [
            {
                "seq": event.seq,
                "payload": _event_payload(event),
                "prev_hash": event.prev_hash,
                "hash": event.hash,
            }
            for event in events
        ]
        updates, report = recompute_chain(records, get_settings().JWT_SECRET_KEY)
        result: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "event_count": report["event_count"],
            "first_bad_seq": report["first_bad_seq"],
            "missing_seqs": report["missing_seqs"],
            "repaired_count": report["repaired_count"],
            "dry_run": dry_run,
        }
        if dry_run or report["first_bad_seq"] is None:
            return result
        by_seq = {event.seq: event for event in events}
        for update in updates:
            event = by_seq[update["seq"]]
            event.prev_hash = update["prev_hash"]
            event.hash = update["hash"]
        await self.session.flush()
        await self.append(
            tenant_id=tenant_id,
            actor_id=None,
            action="audit.chain.repair",
            resource_type="audit_chain",
            resource_id=str(tenant_id),
            data={
                "from_seq": report["first_bad_seq"],
                "to_seq": events[-1].seq,
                "missing_seqs": report["missing_seqs"],
                "repaired_count": report["repaired_count"],
                "reason": "pruned audit rows broke prev_hash links",
            },
        )
        if not await self.verify_chain(tenant_id):
            await self.session.rollback()
            raise AppError(
                "audit chain repair failed self-verification; transaction rolled back",
                code="audit_chain_repair_failed",
            )
        await self.session.commit()
        return result
