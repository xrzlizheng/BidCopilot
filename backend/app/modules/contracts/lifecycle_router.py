"""合同全生命周期 API（独立前缀，避免与既有 /contract-reviews/{review_id} 参数路由冲突）。

prefix=/api/v1/contract-records。由 Lead 在 app/main.py 注册：
    from app.modules.contracts.lifecycle_router import router as contract_lifecycle_router
    app.include_router(contract_lifecycle_router)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.contracts.drafting import DRAFT_DISCLAIMER
from app.modules.contracts.lifecycle import money_str
from app.modules.contracts.lifecycle_export import build_ledger_xlsx
from app.modules.contracts.models import (
    ContractApproval,
    ContractChange,
    ContractDraftClause,
    ContractLedgerEntry,
    ContractRecord,
)
from app.modules.contracts.records_service import (
    add_ledger_entry,
    create_change,
    create_record,
    decide_change,
    generate_draft,
    get_record_or_404,
    ledger_payload,
    list_approvals,
    list_changes,
    list_draft_clauses,
    list_records,
    transition_record,
    update_record,
)
from app.modules.contracts.schemas import (
    ContractActionRequest,
    ContractApprovalResponse,
    ContractChangeCreate,
    ContractChangeListResponse,
    ContractChangeResponse,
    ContractDraftClauseResponse,
    ContractDraftResponse,
    ContractRecordCreate,
    ContractRecordDetailResponse,
    ContractRecordListResponse,
    ContractRecordResponse,
    ContractRecordUpdate,
    LedgerEntryCreate,
    LedgerEntryResponse,
    LedgerListResponse,
    LedgerSummaryResponse,
)
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1/contract-records", tags=["contract-lifecycle"])

LEDGER_DISCLAIMER = "系统生成的履约台账，需财务复核；金额以合同及财务凭证为准"
STATUS_PATTERN = "^(draft|pending_approval|active|expired|terminated|archived)$"


def _record_response(record: ContractRecord) -> ContractRecordResponse:
    return ContractRecordResponse(
        id=record.id,
        project_id=record.project_id,
        review_id=record.review_id,
        contract_no=record.contract_no,
        title=record.title,
        party_a=record.party_a,
        party_b=record.party_b,
        signed_date=record.signed_date,
        effective_date=record.effective_date,
        expiry_date=record.expiry_date,
        amount=money_str(record.amount),
        currency=record.currency,
        status=record.status,
        owner_id=record.owner_id,
        created_by=record.created_by,
        draft_generated_at=record.draft_generated_at,
        draft_disclaimer=record.draft_disclaimer,
        expiry_reminded_at=record.expiry_reminded_at,
        created_at=record.created_at,
    )


def _draft_clause_response(row: ContractDraftClause) -> ContractDraftClauseResponse:
    return ContractDraftClauseResponse(
        id=row.id,
        seq=row.seq,
        title=row.title,
        text=row.text,
        category=row.category,
        source_json=row.source_json or {},
    )


def _record_detail(
    record: ContractRecord, draft: list[ContractDraftClause]
) -> ContractRecordDetailResponse:
    return ContractRecordDetailResponse(
        **_record_response(record).model_dump(),
        draft=[_draft_clause_response(row) for row in draft],
    )


def _approval_response(row: ContractApproval) -> ContractApprovalResponse:
    return ContractApprovalResponse.model_validate(row)


def _ledger_entry_response(row: ContractLedgerEntry) -> LedgerEntryResponse:
    return LedgerEntryResponse(
        id=row.id,
        record_id=row.record_id,
        entry_type=row.entry_type,
        amount=money_str(row.amount) or "0.00",
        occurred_on=row.occurred_on,
        note=row.note,
        voucher_ref=row.voucher_ref,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _change_response(row: ContractChange) -> ContractChangeResponse:
    return ContractChangeResponse(
        id=row.id,
        record_id=row.record_id,
        change_type=row.change_type,
        amount_before=money_str(row.amount_before),
        amount_after=money_str(row.amount_after),
        expiry_before=row.expiry_before,
        expiry_after=row.expiry_after,
        reason=row.reason,
        approval_status=row.approval_status,
        requested_by=row.requested_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        created_at=row.created_at,
    )


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    action: str,
    resource_type: str,
    resource_id: UUID,
    data: dict[str, Any] | None = None,
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        data=data or {},
    )
    await session.commit()


@router.post("", response_model=ContractRecordResponse, status_code=201)
async def post_contract_record(
    body: ContractRecordCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    record = await create_record(
        session, tenant_id=user.tenant_id, user_id=user.id, payload=body
    )
    response = _record_response(record)
    await _audit(
        session,
        user,
        "contract.record_created",
        "contract_record",
        record.id,
        {"contract_no": record.contract_no, "project_id": str(record.project_id)},
    )
    return response


@router.get("", response_model=ContractRecordListResponse)
async def get_contract_records(
    status: str | None = Query(default=None, pattern=STATUS_PATTERN),
    project_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordListResponse:
    rows, total = await list_records(
        session,
        user.tenant_id,
        status=status,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return ContractRecordListResponse(
        items=[_record_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{record_id}", response_model=ContractRecordDetailResponse)
async def get_contract_record(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordDetailResponse:
    record = await get_record_or_404(session, user.tenant_id, record_id)
    draft = await list_draft_clauses(session, user.tenant_id, record_id)
    return _record_detail(record, draft)


@router.patch("/{record_id}", response_model=ContractRecordResponse)
async def patch_contract_record(
    record_id: UUID,
    body: ContractRecordUpdate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    record = await update_record(
        session, tenant_id=user.tenant_id, record_id=record_id, payload=body
    )
    response = _record_response(record)
    await _audit(
        session,
        user,
        "contract.record_updated",
        "contract_record",
        record.id,
        {"fields": sorted(body.model_dump(exclude_unset=True).keys())},
    )
    return response


@router.post("/{record_id}/draft", response_model=ContractDraftResponse)
async def post_contract_draft(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractDraftResponse:
    record, clauses = await generate_draft(
        session, tenant_id=user.tenant_id, record_id=record_id
    )
    response = ContractDraftResponse(
        record=_record_detail(record, clauses),
        disclaimer=DRAFT_DISCLAIMER,
        generated_count=len(clauses),
    )
    await _audit(
        session,
        user,
        "contract.draft_generated",
        "contract_record",
        record.id,
        {"clauses": len(clauses), "disclaimer": DRAFT_DISCLAIMER},
    )
    return response


async def _action(
    *,
    record_id: UUID,
    action: str,
    body: ContractActionRequest | None = None,
    user: CurrentUser,
    session: AsyncSession,
) -> ContractRecordResponse:
    comment = body.comment if body is not None else None
    record, _approval = await transition_record(
        session,
        tenant_id=user.tenant_id,
        record_id=record_id,
        action=action,
        actor_id=user.id,
        comment=comment,
    )
    response = _record_response(record)
    await _audit(
        session,
        user,
        f"contract.{action}",
        "contract_record",
        record.id,
        {"comment": comment, "status": record.status},
    )
    return response


@router.post("/{record_id}/submit", response_model=ContractRecordResponse)
async def post_contract_submit(
    record_id: UUID,
    body: ContractActionRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    return await _action(
        record_id=record_id, action="submit", body=body, user=user, session=session
    )


@router.post("/{record_id}/approve", response_model=ContractRecordResponse)
async def post_contract_approve(
    record_id: UUID,
    body: ContractActionRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    return await _action(
        record_id=record_id, action="approve", body=body, user=user, session=session
    )


@router.post("/{record_id}/reject", response_model=ContractRecordResponse)
async def post_contract_reject(
    record_id: UUID,
    body: ContractActionRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    return await _action(
        record_id=record_id, action="reject", body=body, user=user, session=session
    )


@router.post("/{record_id}/terminate", response_model=ContractRecordResponse)
async def post_contract_terminate(
    record_id: UUID,
    body: ContractActionRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    return await _action(
        record_id=record_id, action="terminate", body=body, user=user, session=session
    )


@router.post("/{record_id}/archive", response_model=ContractRecordResponse)
async def post_contract_archive(
    record_id: UUID,
    body: ContractActionRequest | None = None,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractRecordResponse:
    return await _action(
        record_id=record_id, action="archive", body=body, user=user, session=session
    )


@router.get("/{record_id}/approvals", response_model=list[ContractApprovalResponse])
async def get_contract_approvals(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[ContractApprovalResponse]:
    await get_record_or_404(session, user.tenant_id, record_id)
    rows = await list_approvals(session, user.tenant_id, record_id)
    return [_approval_response(row) for row in rows]


@router.get("/{record_id}/ledger", response_model=LedgerListResponse)
async def get_contract_ledger(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> LedgerListResponse:
    payload = await ledger_payload(session, user.tenant_id, record_id)
    summary = payload["summary"]
    return LedgerListResponse(
        record_id=record_id,
        currency=payload["record"].currency,
        summary=LedgerSummaryResponse(
            contract_amount=money_str(summary["contract_amount"]) or "0.00",
            invoice_total=money_str(summary["invoice_total"]) or "0.00",
            payment_total=money_str(summary["payment_total"]) or "0.00",
            deposit_total=money_str(summary["deposit_total"]) or "0.00",
            retention_total=money_str(summary["retention_total"]) or "0.00",
            outstanding=money_str(summary["outstanding"]) or "0.00",
        ),
        entries=[_ledger_entry_response(row) for row in payload["entries"]],
    )


@router.post("/{record_id}/ledger", response_model=LedgerEntryResponse, status_code=201)
async def post_contract_ledger_entry(
    record_id: UUID,
    body: LedgerEntryCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryResponse:
    entry = await add_ledger_entry(
        session,
        tenant_id=user.tenant_id,
        record_id=record_id,
        user_id=user.id,
        entry_type=body.entry_type,
        amount=body.amount,
        occurred_on=body.occurred_on,
        note=body.note,
        voucher_ref=body.voucher_ref,
    )
    response = _ledger_entry_response(entry)
    await _audit(
        session,
        user,
        "contract.ledger_entry_created",
        "contract_ledger_entry",
        entry.id,
        {"record_id": str(record_id), "entry_type": entry.entry_type, "amount": str(entry.amount)},
    )
    return response


@router.get("/{record_id}/changes", response_model=ContractChangeListResponse)
async def get_contract_changes(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> ContractChangeListResponse:
    await get_record_or_404(session, user.tenant_id, record_id)
    rows = await list_changes(session, user.tenant_id, record_id)
    return ContractChangeListResponse(items=[_change_response(row) for row in rows])


@router.post("/{record_id}/changes", response_model=ContractChangeResponse, status_code=201)
async def post_contract_change(
    record_id: UUID,
    body: ContractChangeCreate,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
) -> ContractChangeResponse:
    change = await create_change(
        session,
        tenant_id=user.tenant_id,
        record_id=record_id,
        user_id=user.id,
        payload=body,
    )
    response = _change_response(change)
    await _audit(
        session,
        user,
        "contract.change_requested",
        "contract_change",
        change.id,
        {"record_id": str(record_id), "change_type": change.change_type},
    )
    return response


@router.post("/{record_id}/changes/{change_id}/approve", response_model=ContractChangeResponse)
async def post_contract_change_approve(
    record_id: UUID,
    change_id: UUID,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> ContractChangeResponse:
    change = await decide_change(
        session,
        tenant_id=user.tenant_id,
        record_id=record_id,
        change_id=change_id,
        action="approve",
        actor_id=user.id,
    )
    response = _change_response(change)
    await _audit(
        session,
        user,
        "contract.change_approved",
        "contract_change",
        change.id,
        {"record_id": str(record_id)},
    )
    return response


@router.post("/{record_id}/changes/{change_id}/reject", response_model=ContractChangeResponse)
async def post_contract_change_reject(
    record_id: UUID,
    change_id: UUID,
    user: CurrentUser = Depends(require_permission("project:approve")),
    session: AsyncSession = Depends(get_session),
) -> ContractChangeResponse:
    change = await decide_change(
        session,
        tenant_id=user.tenant_id,
        record_id=record_id,
        change_id=change_id,
        action="reject",
        actor_id=user.id,
    )
    response = _change_response(change)
    await _audit(
        session,
        user,
        "contract.change_rejected",
        "contract_change",
        change.id,
        {"record_id": str(record_id)},
    )
    return response


@router.get("/{record_id}/export")
async def export_contract_ledger(
    record_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    payload = await ledger_payload(session, user.tenant_id, record_id)
    record = payload["record"]
    entries = [
        {
            "entry_type": row.entry_type,
            "amount": row.amount,
            "occurred_on": row.occurred_on,
            "note": row.note,
            "voucher_ref": row.voucher_ref,
            "created_at": row.created_at.isoformat(),
        }
        for row in payload["entries"]
    ]
    data = build_ledger_xlsx(
        contract_no=record.contract_no,
        title=record.title,
        currency=record.currency,
        entries=entries,
        summary=payload["summary"],
        disclaimer=LEDGER_DISCLAIMER,
    )
    await _audit(
        session,
        user,
        "contract.ledger_exported",
        "contract_record",
        record.id,
        {"entries": len(entries)},
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="contract-ledger-{record_id}.xlsx"'
        },
    )


