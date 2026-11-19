from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.projects.export_branding import resolve_export_branding
from app.modules.projects.readiness import (
    ensure_readiness,
    get_template,
    readiness_payload,
    refresh_auto_items,
)
from app.modules.projects.receipts import (
    attachment_summary,
    list_attachments_by_receipt,
    list_receipts,
    receipt_payload,
)
from app.modules.projects.service import get_project


async def build_delivery_summary(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
) -> dict:
    project = await get_project(session, tenant_id, project_id)
    # 品牌解析只保留一处真相：与 docx/xlsx 导出共用同一个解析函数，
    # 避免「交付页显示的牌子」与「导出文件里的牌子」不一致。
    branding = await resolve_export_branding(session, tenant_id, project_id)

    items, _ = await ensure_readiness(session, tenant_id, project_id)
    items = await refresh_auto_items(session, tenant_id, project_id)
    template = get_template(items[0].template_code if items else "it_integration")
    readiness = readiness_payload(project, template, items)
    receipts = await list_receipts(session, tenant_id, project_id)
    latest = receipts[0] if receipts else None
    grouped = await list_attachments_by_receipt(
        session, tenant_id, [receipt.id for receipt in receipts]
    )
    all_attachments = [item for rows in grouped.values() for item in rows]
    receipt_attachments = grouped.get(latest.id, []) if latest else []

    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "code": project.code,
            "buyer": project.buyer,
            "status": project.status,
            "bid_deadline": project.bid_deadline,
        },
        "brand": {
            "name": branding.name,
            "logo_url": branding.logo_url,
            "logo_embedded": branding.has_logo,
        },
        "readiness": readiness["summary"],
        "template": readiness["template"],
        "latest_receipt": receipt_payload(latest, receipt_attachments) if latest else None,
        # 交付摘要要能回答「回执证据齐不齐」：附件总数、字节数与按 kind 的分布。
        # 系统不代替用户向交易平台上传，这里只是人工留痕的汇总。
        "receipt_attachments": attachment_summary(all_attachments),
        "generated_at": datetime.now(UTC),
    }
