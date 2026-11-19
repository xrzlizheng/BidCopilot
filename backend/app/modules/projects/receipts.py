"""交标回执与回执附件（人工留痕）。

附件的边界：
- 只做「人工证据」留存，系统不代替用户向任何交易平台上传或确认递交；
- 类型只信文件头魔数：客户端声明的 content-type 必须与魔数推断结果一致，
  否则拒绝——只信 header 等于允许把任意内容伪装成图片/PDF 存进对象存储；
- 存储 key 由服务端生成（租户/回执/uuid+规范后缀），用户文件名只用于展示，
  避免路径穿越与同名覆盖；
- 内容经 `app.modules.gateway.snapshots.build_snapshot_store` 落对象存储，
  与交标自查快照同源，禁止旁路写磁盘。
"""

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import AppError, NotFoundError, ServiceUnavailableError
from app.modules.gateway.snapshots import build_snapshot_store
from app.modules.notifications import webhooks
from app.modules.projects.models import SubmissionReceipt
from app.modules.projects.readiness import (
    ensure_readiness,
    get_template,
    readiness_payload,
    refresh_auto_items,
)
from app.modules.projects.receipt_models import SubmissionReceiptAttachment
from app.modules.projects.schemas import SubmissionReceiptCreate
from app.modules.projects.service import get_project

logger = logging.getLogger("bidcopilot.projects.receipts")

ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
ATTACHMENT_KEY_PREFIX = "receipt-attachments/"
ATTACHMENT_KINDS = ("receipt", "signature", "encryption", "other")

# 魔数 -> 规范 content-type / 存储后缀。顺序无副作用，PDF 与 PNG 前缀不重叠。
_MAGIC_TO_TYPE: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"%PDF-", "application/pdf", ".pdf"),
)
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"
_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "application/pdf", "image/webp"}
_CONTENT_TYPE_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
_SUFFIX_BY_TYPE = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf", "image/webp": ".webp"}


class ReceiptAttachmentError(AppError):
    """附件校验失败（大小/类型/kind）。400 而非 422：这是字节内容层面的拒绝。"""

    status_code = 400
    code = "invalid_receipt_attachment"


def normalize_content_type(content_type: str | None) -> str:
    """只取 MIME 主体并归一化 jpg 别名；未知类型原样返回（由白名单拒绝）。"""
    mime = (content_type or "").split(";")[0].strip().lower()
    return _CONTENT_TYPE_ALIASES.get(mime, mime)


def detect_attachment_type(content: bytes) -> str | None:
    """用文件头魔数推断真实类型；返回 None 表示不在白名单内。"""
    for magic, mime, _ in _MAGIC_TO_TYPE:
        if content.startswith(magic):
            return mime
    if len(content) >= 12 and content[:4] == _WEBP_RIFF and content[8:12] == _WEBP_TAG:
        return "image/webp"
    return None


def safe_display_filename(filename: str | None) -> str:
    """展示用文件名：去目录、去控制字符、限长。不参与存储 key 生成。"""
    base = Path((filename or "").replace("\\", "/")).name.strip()
    cleaned = "".join(char for char in base if char >= " " and char != "\x7f")
    return cleaned[:200]


def attachment_payload(attachment: SubmissionReceiptAttachment) -> dict:
    # 刻意不返回 storage_ref：对象存储位置属于内部实现，外部只认 attachment_id。
    return {
        "id": attachment.id,
        "receipt_id": attachment.receipt_id,
        "kind": attachment.kind,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "sha256": attachment.sha256,
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at,
    }


def attachment_summary(attachments: list[SubmissionReceiptAttachment]) -> dict:
    counts = Counter(attachment.kind for attachment in attachments)
    return {
        "total": len(attachments),
        "total_bytes": sum(attachment.size_bytes for attachment in attachments),
        "by_kind": {kind: counts.get(kind, 0) for kind in ATTACHMENT_KINDS},
    }


async def create_receipt(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    body: SubmissionReceiptCreate,
) -> SubmissionReceipt:
    project = await get_project(session, tenant_id, project_id)
    items, _ = await ensure_readiness(session, tenant_id, project_id)
    items = await refresh_auto_items(session, tenant_id, project_id)
    template = get_template(items[0].template_code if items else "it_integration")
    snapshot = readiness_payload(project, template, items)
    receipt = SubmissionReceipt(
        tenant_id=tenant_id,
        project_id=project_id,
        submitted_at=body.submitted_at,
        platform=body.platform.strip(),
        receipt_no=body.receipt_no.strip() if body.receipt_no else None,
        notes=body.notes,
        operator_id=user_id,
        checklist_snapshot=snapshot,
    )
    session.add(receipt)
    await session.commit()
    await session.refresh(receipt)
    # 交标回执事件：落库成功后登记后台投递，失败不影响主流程。
    webhooks.notify_submission_receipt_created(
        tenant_id,
        project_id=project_id,
        receipt_id=receipt.id,
        platform=receipt.platform,
        receipt_no=receipt.receipt_no,
        submitted_at=receipt.submitted_at.isoformat() if receipt.submitted_at else None,
    )
    return receipt


async def list_receipts(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
) -> list[SubmissionReceipt]:
    await get_project(session, tenant_id, project_id)
    return list(
        (
            await session.execute(
                select(SubmissionReceipt)
                .where(
                    SubmissionReceipt.tenant_id == tenant_id,
                    SubmissionReceipt.project_id == project_id,
                )
                .order_by(SubmissionReceipt.submitted_at.desc(), SubmissionReceipt.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_receipt(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    receipt_id: UUID,
) -> SubmissionReceipt:
    """回执归属校验：跨租户、跨项目一律 404，不泄露存在性。"""
    await get_project(session, tenant_id, project_id)
    receipt = (
        await session.execute(
            select(SubmissionReceipt).where(
                SubmissionReceipt.id == receipt_id,
                SubmissionReceipt.tenant_id == tenant_id,
                SubmissionReceipt.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise NotFoundError()
    return receipt


async def list_attachments_by_receipt(
    session: AsyncSession,
    tenant_id: UUID,
    receipt_ids: list[UUID],
) -> dict[UUID, list[SubmissionReceiptAttachment]]:
    """一次批量查询取全部附件，避免回执列表 N+1。"""
    if not receipt_ids:
        return {}
    rows = list(
        (
            await session.execute(
                select(SubmissionReceiptAttachment)
                .where(
                    SubmissionReceiptAttachment.tenant_id == tenant_id,
                    SubmissionReceiptAttachment.receipt_id.in_(receipt_ids),
                )
                .order_by(
                    SubmissionReceiptAttachment.created_at.asc(),
                    SubmissionReceiptAttachment.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[UUID, list[SubmissionReceiptAttachment]] = {}
    for row in rows:
        grouped.setdefault(row.receipt_id, []).append(row)
    return grouped


async def get_attachment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    receipt_id: UUID,
    attachment_id: UUID,
) -> SubmissionReceiptAttachment:
    attachment = (
        await session.execute(
            select(SubmissionReceiptAttachment).where(
                SubmissionReceiptAttachment.id == attachment_id,
                SubmissionReceiptAttachment.tenant_id == tenant_id,
                SubmissionReceiptAttachment.receipt_id == receipt_id,
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError()
    return attachment


async def upload_attachment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    receipt_id: UUID,
    user_id: UUID,
    kind: str,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> SubmissionReceiptAttachment:
    # 先做归属校验：跨租户/跨项目一律 404，优先于任何参数校验，避免泄露存在性。
    receipt = await get_receipt(
        session, tenant_id=tenant_id, project_id=project_id, receipt_id=receipt_id
    )
    if kind not in ATTACHMENT_KINDS:
        raise ReceiptAttachmentError(f"附件类型必须是 {' / '.join(ATTACHMENT_KINDS)} 之一")
    if not content:
        raise ReceiptAttachmentError("附件内容为空")
    if len(content) > ATTACHMENT_MAX_BYTES:
        raise ReceiptAttachmentError("附件大小超过 10MB 上限")

    detected = detect_attachment_type(content)
    if detected is None:
        raise ReceiptAttachmentError("仅支持 PNG / JPEG / PDF / WebP 附件")
    declared = normalize_content_type(content_type)
    if declared not in _ALLOWED_CONTENT_TYPES:
        raise ReceiptAttachmentError("仅支持 PNG / JPEG / PDF / WebP 附件")
    if declared != detected:
        # 只信文件头：声明 image/png 但内容不是 PNG，或声明 PDF 实际是图片，都拒绝。
        raise ReceiptAttachmentError("附件类型与文件内容不一致")

    display_name = safe_display_filename(filename) or f"attachment{_SUFFIX_BY_TYPE[detected]}"
    digest = hashlib.sha256(content).hexdigest()
    # key 完全由服务端生成：租户/回执/uuid+规范后缀，用户文件名不参与，杜绝路径穿越。
    object_key = (
        f"{ATTACHMENT_KEY_PREFIX}{tenant_id}/{receipt.id}/{uuid4().hex}{_SUFFIX_BY_TYPE[detected]}"
    )
    try:
        store = build_snapshot_store(get_settings())
        storage_ref = await store.put_bytes(object_key, content, detected)
    except Exception as exc:
        raise ServiceUnavailableError("附件存储不可用") from exc

    attachment = SubmissionReceiptAttachment(
        tenant_id=tenant_id,
        receipt_id=receipt.id,
        kind=kind,
        filename=display_name,
        content_type=detected,
        size_bytes=len(content),
        sha256=digest,
        storage_ref=storage_ref,
        uploaded_by=user_id,
    )
    session.add(attachment)
    try:
        await session.commit()
    except Exception:
        # 元数据没落库就别留孤儿对象。
        await session.rollback()
        try:
            await store.delete_bytes(storage_ref)
        except Exception:
            logger.warning("receipt attachment orphan object: %s", storage_ref, exc_info=True)
        raise
    await session.refresh(attachment)
    return attachment


async def read_attachment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    receipt_id: UUID,
    attachment_id: UUID,
) -> tuple[SubmissionReceiptAttachment, bytes]:
    await get_receipt(
        session, tenant_id=tenant_id, project_id=project_id, receipt_id=receipt_id
    )
    attachment = await get_attachment(
        session, tenant_id=tenant_id, receipt_id=receipt_id, attachment_id=attachment_id
    )
    try:
        store = build_snapshot_store(get_settings())
        content = await store.get_bytes(attachment.storage_ref)
    except Exception as exc:
        raise ServiceUnavailableError("附件存储不可用") from exc
    return attachment, content


async def delete_attachment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    receipt_id: UUID,
    attachment_id: UUID,
) -> SubmissionReceiptAttachment:
    await get_receipt(
        session, tenant_id=tenant_id, project_id=project_id, receipt_id=receipt_id
    )
    attachment = await get_attachment(
        session, tenant_id=tenant_id, receipt_id=receipt_id, attachment_id=attachment_id
    )
    # 先删对象再删记录：对象删除失败时保留记录，避免留下「记录没了但内容还在」的悬空引用。
    try:
        store = build_snapshot_store(get_settings())
        await store.delete_bytes(attachment.storage_ref)
    except Exception as exc:
        raise ServiceUnavailableError("附件存储不可用") from exc
    await session.delete(attachment)
    await session.commit()
    return attachment


def attachment_disposition(filename: str) -> str:
    """Content-Disposition：ASCII 兜底 + RFC 5987 UTF-8 文件名，避免中文名丢失/注入。"""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "attachment"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def receipt_payload(
    receipt: SubmissionReceipt,
    attachments: list[SubmissionReceiptAttachment] | None = None,
) -> dict:
    snapshot = receipt.checklist_snapshot or {}
    return {
        "id": receipt.id,
        "project_id": receipt.project_id,
        "submitted_at": receipt.submitted_at,
        "platform": receipt.platform,
        "receipt_no": receipt.receipt_no,
        "notes": receipt.notes,
        "operator_id": receipt.operator_id,
        "checklist_summary": snapshot.get("summary") or {},
        "created_at": receipt.created_at,
        "attachments": [attachment_payload(item) for item in attachments or []],
    }


async def list_receipt_payloads(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
) -> list[dict]:
    receipts = await list_receipts(session, tenant_id, project_id)
    grouped = await list_attachments_by_receipt(
        session, tenant_id, [receipt.id for receipt in receipts]
    )
    return [receipt_payload(receipt, grouped.get(receipt.id, [])) for receipt in receipts]
