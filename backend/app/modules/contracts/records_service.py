"""合同全生命周期 DB 编排：主数据、起草、审批、履约台账、变更/续签、到期提醒。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.repository import TenantRepository
from app.modules.contracts.drafting import DRAFT_DISCLAIMER, build_contract_draft
from app.modules.contracts.lifecycle import (
    ensure_transition,
    is_in_expiry_window,
    next_change_approval,
    resolve_action,
    resolve_expiry_reminder_days,
    summarize_ledger,
    validate_change,
)
from app.modules.contracts.models import (
    ContractApproval,
    ContractChange,
    ContractDraftClause,
    ContractLedgerEntry,
    ContractRecord,
)
from app.modules.contracts.schemas import (
    ContractChangeCreate,
    ContractRecordCreate,
    ContractRecordUpdate,
)
from app.modules.contracts.service import get_clauses, get_review
from app.modules.notifications.service import NotificationService
from app.modules.projects.commitments import list_commitments
from app.modules.projects.service import get_project
from app.modules.system.models import TenantSetting


class ContractRecordRepository(TenantRepository[ContractRecord]):
    model = ContractRecord


class ContractDraftClauseRepository(TenantRepository[ContractDraftClause]):
    model = ContractDraftClause


class ContractApprovalRepository(TenantRepository[ContractApproval]):
    model = ContractApproval


class ContractLedgerRepository(TenantRepository[ContractLedgerEntry]):
    model = ContractLedgerEntry


class ContractChangeRepository(TenantRepository[ContractChange]):
    model = ContractChange


class TenantSettingRepository(TenantRepository[TenantSetting]):
    model = TenantSetting


async def get_record_or_404(session: AsyncSession, tenant_id: UUID, record_id: UUID) -> ContractRecord:
    return await ContractRecordRepository(session, tenant_id).get_or_404(record_id)


async def _record_for_update(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> ContractRecord:
    row = (
        await session.execute(
            ContractRecordRepository(session, tenant_id)
            .scoped(select(ContractRecord).where(ContractRecord.id == record_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    return row


async def list_records(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    status: str | None = None,
    project_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ContractRecord], int]:
    repo = ContractRecordRepository(session, tenant_id)
    statement = repo.scoped(select(ContractRecord))
    count_statement = repo.scoped(select(func.count()).select_from(ContractRecord))
    if status:
        statement = statement.where(ContractRecord.status == status)
        count_statement = count_statement.where(ContractRecord.status == status)
    if project_id:
        statement = statement.where(ContractRecord.project_id == project_id)
        count_statement = count_statement.where(ContractRecord.project_id == project_id)
    rows = list(
        (
            await session.execute(
                statement.order_by(ContractRecord.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )
    total = int((await session.execute(count_statement)).scalar_one())
    return rows, total


async def _resolve_review(
    session: AsyncSession, tenant_id: UUID, project_id: UUID, review_id: UUID | None
) -> None:
    if review_id is None:
        return
    review = await get_review(session, tenant_id=tenant_id, review_id=review_id)
    if review is None:
        raise NotFoundError()
    if review.project_id != project_id:
        raise ValidationAppError("关联的合同审查不属于该项目", code="review_project_mismatch")


async def create_record(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, payload: ContractRecordCreate
) -> ContractRecord:
    await get_project(session, tenant_id, payload.project_id)
    await _resolve_review(session, tenant_id, payload.project_id, payload.review_id)
    record = ContractRecord(
        tenant_id=tenant_id,
        project_id=payload.project_id,
        review_id=payload.review_id,
        contract_no=payload.contract_no,
        title=payload.title,
        party_a=payload.party_a,
        party_b=payload.party_b,
        signed_date=payload.signed_date,
        effective_date=payload.effective_date,
        expiry_date=payload.expiry_date,
        amount=payload.amount,
        currency=payload.currency,
        status="draft",
        owner_id=payload.owner_id,
        created_by=user_id,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("合同编号已存在", code="duplicate_contract_no") from exc
    await session.refresh(record)
    return record


async def update_record(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    record_id: UUID,
    payload: ContractRecordUpdate,
) -> ContractRecord:
    record = await _record_for_update(session, tenant_id, record_id)
    data = payload.model_dump(exclude_unset=True)
    if "review_id" in data:
        await _resolve_review(session, tenant_id, record.project_id, data["review_id"])
    for key, value in data.items():
        setattr(record, key, value)
    if record.effective_date and record.expiry_date and record.expiry_date < record.effective_date:
        await session.rollback()
        raise ValidationAppError("到期日不能早于生效日", code="invalid_contract_dates")
    if record.signed_date and record.expiry_date and record.expiry_date < record.signed_date:
        await session.rollback()
        raise ValidationAppError("到期日不能早于签约日", code="invalid_contract_dates")
    await session.commit()
    await session.refresh(record)
    return record


async def list_draft_clauses(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> list[ContractDraftClause]:
    return list(
        (
            await session.execute(
                ContractDraftClauseRepository(session, tenant_id)
                .scoped(select(ContractDraftClause))
                .where(ContractDraftClause.record_id == record_id)
                .order_by(ContractDraftClause.seq)
            )
        ).scalars()
    )


async def generate_draft(
    session: AsyncSession, *, tenant_id: UUID, record_id: UUID
) -> tuple[ContractRecord, list[ContractDraftClause]]:
    record = await _record_for_update(session, tenant_id, record_id)
    if record.status != "draft":
        await session.rollback()
        raise ConflictError(
            "仅草稿状态可重新生成合同初稿", code="draft_not_allowed"
        )
    commitments = await list_commitments(session, tenant_id, record.project_id)
    review_clauses = await get_clauses(session, record.review_id) if record.review_id else []
    record_payload = {
        "project_id": str(record.project_id),
        "review_id": str(record.review_id) if record.review_id else None,
        "contract_no": record.contract_no,
        "title": record.title,
        "party_a": record.party_a,
        "party_b": record.party_b,
        "amount": record.amount,
        "currency": record.currency,
    }
    clauses = build_contract_draft(
        record=record_payload,
        commitments=commitments,
        review_clauses=review_clauses,
    )
    for clause in clauses:
        if not clause.get("source_json"):
            await session.rollback()
            raise ValidationAppError("禁止生成无来源条款", code="clause_source_required")

    existing = await list_draft_clauses(session, tenant_id, record_id)
    for row in existing:
        await session.delete(row)
    await session.flush()
    persisted: list[ContractDraftClause] = []
    for clause in clauses:
        row = ContractDraftClause(
            tenant_id=tenant_id,
            record_id=record.id,
            seq=int(clause["seq"]),
            title=str(clause["title"]),
            text=str(clause["text"]),
            category=str(clause.get("category") or "other"),
            source_json=clause["source_json"],
        )
        session.add(row)
        persisted.append(row)
    record.draft_generated_at = datetime.now(UTC)
    record.draft_disclaimer = DRAFT_DISCLAIMER
    await session.commit()
    for row in persisted:
        await session.refresh(row)
    await session.refresh(record)
    return record, persisted


async def transition_record(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    record_id: UUID,
    action: str,
    actor_id: UUID,
    comment: str | None,
) -> tuple[ContractRecord, ContractApproval]:
    record = await _record_for_update(session, tenant_id, record_id)
    from_status = record.status
    target = resolve_action(from_status, action)
    record.status = target
    approval = ContractApproval(
        tenant_id=tenant_id,
        record_id=record.id,
        action=action,
        actor_id=actor_id,
        comment=comment,
        from_status=from_status,
        to_status=target,
    )
    session.add(approval)
    await session.commit()
    await session.refresh(record)
    await session.refresh(approval)
    return record, approval


async def list_approvals(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> list[ContractApproval]:
    return list(
        (
            await session.execute(
                ContractApprovalRepository(session, tenant_id)
                .scoped(select(ContractApproval))
                .where(ContractApproval.record_id == record_id)
                .order_by(ContractApproval.created_at, ContractApproval.id)
            )
        ).scalars()
    )


async def list_ledger(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> list[ContractLedgerEntry]:
    return list(
        (
            await session.execute(
                ContractLedgerRepository(session, tenant_id)
                .scoped(select(ContractLedgerEntry))
                .where(ContractLedgerEntry.record_id == record_id)
                .order_by(ContractLedgerEntry.occurred_on, ContractLedgerEntry.created_at)
            )
        ).scalars()
    )


async def add_ledger_entry(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    record_id: UUID,
    user_id: UUID,
    entry_type: str,
    amount: Decimal,
    occurred_on: date,
    note: str | None,
    voucher_ref: str | None,
) -> ContractLedgerEntry:
    await get_record_or_404(session, tenant_id, record_id)
    entry = ContractLedgerEntry(
        tenant_id=tenant_id,
        record_id=record_id,
        entry_type=entry_type,
        amount=amount,
        occurred_on=occurred_on,
        note=note,
        voucher_ref=voucher_ref,
        created_by=user_id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def ledger_payload(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> dict[str, Any]:
    record = await get_record_or_404(session, tenant_id, record_id)
    entries = await list_ledger(session, tenant_id, record_id)
    summary = summarize_ledger(entries, contract_amount=record.amount)
    return {"record": record, "entries": entries, "summary": summary}


async def list_changes(
    session: AsyncSession, tenant_id: UUID, record_id: UUID
) -> list[ContractChange]:
    return list(
        (
            await session.execute(
                ContractChangeRepository(session, tenant_id)
                .scoped(select(ContractChange))
                .where(ContractChange.record_id == record_id)
                .order_by(ContractChange.created_at, ContractChange.id)
            )
        ).scalars()
    )


async def create_change(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    record_id: UUID,
    user_id: UUID,
    payload: ContractChangeCreate,
) -> ContractChange:
    record = await _record_for_update(session, tenant_id, record_id)
    if record.status == "archived":
        await session.rollback()
        raise ConflictError("已归档合同不能新增变更", code="contract_archived")
    amount_before = payload.amount_before if payload.amount_before is not None else record.amount
    expiry_before = payload.expiry_before if payload.expiry_before is not None else record.expiry_date
    validate_change(
        change_type=payload.change_type,
        amount_before=amount_before,
        amount_after=payload.amount_after,
        expiry_before=expiry_before,
        expiry_after=payload.expiry_after,
        reason=payload.reason,
    )
    change = ContractChange(
        tenant_id=tenant_id,
        record_id=record.id,
        change_type=payload.change_type,
        amount_before=amount_before,
        amount_after=payload.amount_after,
        expiry_before=expiry_before,
        expiry_after=payload.expiry_after,
        reason=payload.reason,
        approval_status="pending",
        requested_by=user_id,
    )
    session.add(change)
    await session.commit()
    await session.refresh(change)
    return change


async def get_change_or_404(
    session: AsyncSession, tenant_id: UUID, record_id: UUID, change_id: UUID
) -> ContractChange:
    row = (
        await session.execute(
            ContractChangeRepository(session, tenant_id)
            .scoped(select(ContractChange))
            .where(
                ContractChange.id == change_id,
                ContractChange.record_id == record_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    return row


async def decide_change(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    record_id: UUID,
    change_id: UUID,
    action: str,
    actor_id: UUID,
) -> ContractChange:
    # 行锁：approve/reject 并发时只有一个能通过 pending 状态校验并落金额。
    change = (
        await session.execute(
            ContractChangeRepository(session, tenant_id)
            .scoped(select(ContractChange))
            .where(
                ContractChange.id == change_id,
                ContractChange.record_id == record_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if change is None:
        raise NotFoundError()
    target = next_change_approval(change.approval_status, action)
    change.approval_status = target
    change.approved_by = actor_id
    change.approved_at = datetime.now(UTC)
    if target == "approved":
        record = await _record_for_update(session, tenant_id, record_id)
        if change.amount_after is not None:
            record.amount = change.amount_after
        if change.expiry_after is not None:
            record.expiry_date = change.expiry_after
    await session.commit()
    await session.refresh(change)
    return change


async def get_expiry_reminder_days(session: AsyncSession, tenant_id: UUID) -> int:
    row = (
        await session.execute(
            TenantSettingRepository(session, tenant_id)
            .scoped(select(TenantSetting))
            .where(TenantSetting.key == "contract_expiry_reminder")
        )
    ).scalar_one_or_none()
    return resolve_expiry_reminder_days(row.value if row is not None else None)


async def scan_contract_expiries(
    session: AsyncSession, *, now: datetime, tenant_id: UUID | None = None
) -> int:
    """到期前 N 天写消息中心；到期未续签则落 expired。调度器每日调用。"""
    today = now.date()
    tenant_statement = select(ContractRecord.tenant_id).where(ContractRecord.status == "active").distinct()
    if tenant_id is not None:
        tenant_statement = tenant_statement.where(ContractRecord.tenant_id == tenant_id)
    tenant_ids = list((await session.execute(tenant_statement)).scalars())
    sent = 0
    for current_tenant_id in tenant_ids:
        days = await get_expiry_reminder_days(session, current_tenant_id)
        rows = list(
            (
                await session.execute(
                    ContractRecordRepository(session, current_tenant_id)
                    .scoped(select(ContractRecord))
                    .where(
                        ContractRecord.status == "active",
                        ContractRecord.expiry_date.is_not(None),
                    )
                    .limit(500)
                )
            ).scalars()
        )
        changed = False
        for record in rows:
            record_id = record.id
            expiry_date = record.expiry_date
            owner_id = record.owner_id or record.created_by
            contract_no = record.contract_no
            title = record.title
            currency = record.currency
            if expiry_date is None:
                continue
            if expiry_date < today:
                record.status = ensure_transition(record.status, "expired")
                changed = True
                continue
            if record.expiry_reminded_at is not None:
                continue
            if not is_in_expiry_window(expiry_date, today, days):
                continue
            if owner_id is None:
                continue
            # 先打标再 notify：notify 内部会 commit，避免 commit 后访问已过期 ORM 实例。
            record.expiry_reminded_at = now
            await NotificationService(session).notify(
                tenant_id=current_tenant_id,
                user_id=owner_id,
                notification_type="contract_expiry_reminder",
                title=f"合同即将到期：{contract_no}",
                body=(
                    f"合同「{title}」将于 {expiry_date.isoformat()} 到期"
                    f"（提前 {days} 天提醒，币种 {currency}），请安排续签或归档。"
                ),
                resource_type="contract_record",
                resource_id=str(record_id),
            )
            changed = True
            sent += 1
        if changed:
            await session.commit()
    return sent
