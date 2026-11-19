from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.assets.repository import CompanyRepository
from app.modules.generation.formatting import _set_east_asia, apply_format
from app.modules.pricing.cost_model import MONEY_QUANT
from app.modules.pricing.engine import TIER_LABELS, money_text
from app.modules.pricing.models import PriceQuote
from app.modules.pricing.repository import PriceQuoteRepository
from app.modules.pricing.schemas import QuoteGenerateRequest, QuoteItemInput
from app.modules.pricing.service import latest_strategy
from app.modules.projects.service import get_project

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_GROUP_UNITS = ("仟", "佰", "拾", "")
_BIG_UNITS = ("", "万", "亿", "兆", "京")


def _group_text(group: int) -> str:
    digits = ((group // 1000) % 10, (group // 100) % 10, (group // 10) % 10, group % 10)
    parts: list[str] = []
    zero_pending = False
    for digit, unit in zip(digits, _GROUP_UNITS):
        if digit == 0:
            zero_pending = bool(parts)
        else:
            if zero_pending:
                parts.append("零")
            parts.append(_DIGITS[digit] + unit)
            zero_pending = False
    return "".join(parts)


def rmb_upper(amount: Decimal) -> str:
    """人民币大写金额（元/角/分），覆盖 Numeric(18,2) 全量程。"""
    if amount < 0:
        raise ValueError("金额不能为负")
    amount = amount.quantize(MONEY_QUANT)
    integer_part = int(amount)
    jiao, fen = divmod(int((amount - integer_part) * 100), 10)

    if integer_part == 0:
        integer_text = "零"
    else:
        groups: list[int] = []
        remaining = integer_part
        while remaining > 0:
            groups.append(remaining % 10000)
            remaining //= 10000
        parts: list[str] = []
        zero_pending = False
        for index in range(len(groups) - 1, -1, -1):
            group = groups[index]
            if group == 0:
                zero_pending = bool(parts)
                continue
            if parts and (zero_pending or group < 1000):
                parts.append("零")
            parts.append(_group_text(group) + _BIG_UNITS[index])
            zero_pending = False
        integer_text = "".join(parts)

    text = integer_text + "元"
    if jiao == 0 and fen == 0:
        return text + "整"
    if jiao > 0:
        text += _DIGITS[jiao] + "角"
    elif integer_part > 0:
        text += "零"
    if fen > 0:
        text += _DIGITS[fen] + "分"
    else:
        text += "整"
    return text


def resolve_quote_total(
    strategy_result: dict[str, Any],
    tier: str | None,
    override: Decimal | None,
) -> tuple[Decimal, str]:
    """总价优先取显式报价，否则取策略档位价；档位不可用时显式报错，不静默换档。"""
    if override is not None:
        return override.quantize(MONEY_QUANT), "input"
    key = tier or "balanced"
    label = TIER_LABELS.get(key, key)
    entry = next(
        (item for item in strategy_result.get("tiers") or [] if item.get("tier") == key),
        None,
    )
    if entry is None or entry.get("price") is None:
        reason = (entry or {}).get("reason") or "策略未给出该档位建议价"
        raise ValidationAppError(f"「{label}」档不可用：{reason}。请改用其他档位或显式填写报价金额")
    if entry.get("status") != "ok":
        raise ValidationAppError(
            f"「{label}」档不可执行（{'；'.join(entry.get('risks') or []) or '数据不足'}）。"
            "请改用其他档位或显式填写报价金额"
        )
    return Decimal(str(entry["price"])).quantize(MONEY_QUANT), f"tier:{key}"


def build_quote_items(
    items: list[QuoteItemInput], total: Decimal, default_name: str
) -> list[dict[str, str]]:
    if not items:
        return [
            {
                "name": default_name,
                "spec": "",
                "quantity": "1",
                "unit": "项",
                "unit_price": money_text(total) or "0.00",
                "amount": money_text(total) or "0.00",
            }
        ]
    rows: list[dict[str, str]] = []
    subtotal = Decimal("0")
    for item in items:
        amount = (item.quantity * item.unit_price).quantize(MONEY_QUANT)
        subtotal += amount
        rows.append(
            {
                "name": item.name.strip(),
                "spec": item.spec.strip(),
                "quantity": format(item.quantity, "f"),
                "unit": item.unit.strip(),
                "unit_price": money_text(item.unit_price) or "0.00",
                "amount": money_text(amount) or "0.00",
            }
        )
    if subtotal != total:
        raise ValidationAppError(
            f"报价明细合计 {money_text(subtotal)} 元与报价总额 {money_text(total)} 元不一致，"
            "请调整明细或总额后重试"
        )
    return rows


async def generate_quote(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    body: QuoteGenerateRequest,
) -> PriceQuote:
    project = await get_project(session, tenant_id, project_id)
    strategy = await latest_strategy(session, tenant_id, project_id)
    total, price_source = resolve_quote_total(
        strategy.result_json or {}, body.tier, body.quote_amount
    )
    items = build_quote_items(body.items, total, f"{project.name}实施服务")

    supplier_name = (body.supplier_name or "").strip() or None
    if supplier_name is None and project.company_id is not None:
        company = await CompanyRepository(session, tenant_id).get_or_404(project.company_id)
        supplier_name = company.name

    now = datetime.now(UTC)
    row = PriceQuote(
        tenant_id=tenant_id,
        project_id=project_id,
        strategy_id=strategy.id,
        quote_number=f"BJ-{now:%Y%m%d}-{uuid4().hex[:8].upper()}",
        project_name=project.name,
        buyer_name=project.buyer,
        supplier_name=supplier_name,
        total_amount=total,
        amount_in_words=rmb_upper(total),
        items_json=items,
        tier=None if price_source == "input" else (body.tier or "balanced"),
        price_source=price_source,
        valid_until=now + timedelta(days=body.validity_days),
        notes=(body.notes or "").strip() or None,
        created_by=user_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def latest_quote(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> PriceQuote:
    await get_project(session, tenant_id, project_id)
    row = (
        await session.execute(
            PriceQuoteRepository(session, tenant_id)
            .scoped(select(PriceQuote))
            .where(PriceQuote.project_id == project_id)
            .order_by(PriceQuote.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("尚未生成报价单")
    return row


def serialize_quote(row: PriceQuote) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "strategy_id": str(row.strategy_id) if row.strategy_id else None,
        "quote_number": row.quote_number,
        "project_name": row.project_name,
        "buyer_name": row.buyer_name,
        "supplier_name": row.supplier_name,
        "total_amount": money_text(row.total_amount),
        "currency": row.currency,
        "amount_in_words": row.amount_in_words,
        "items": row.items_json or [],
        "tier": row.tier,
        "price_source": row.price_source,
        "valid_until": row.valid_until.isoformat() if row.valid_until else None,
        "notes": row.notes,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _fill_cell(cell: Any, text: str, *, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.first_line_indent = Pt(0)
    run = paragraph.add_run(text)
    run.bold = bold
    _set_east_asia(run, "宋体")


def build_quote_document(row: PriceQuote, project_code: str | None) -> Any:
    document = Document()
    apply_format(
        document,
        {"body": {"font": "宋体", "size_pt": 10.5, "line_spacing": 1.15, "first_line_indent_chars": 0}},
    )

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.first_line_indent = Pt(0)
    run = title.add_run("报 价 单")
    run.bold = True
    run.font.size = Pt(18)
    _set_east_asia(run, "黑体")

    meta = document.add_table(rows=0, cols=2)
    meta.style = "Table Grid"
    meta_rows = [
        ("项目名称", row.project_name),
        ("项目编号", project_code or "—"),
        ("采购人", row.buyer_name or "—"),
        ("供应商", row.supplier_name or "—"),
        ("报价单编号", row.quote_number),
        ("报价日期", row.created_at.strftime("%Y-%m-%d") if row.created_at else "—"),
        ("报价有效期", f"至 {row.valid_until:%Y-%m-%d}" if row.valid_until else "—"),
    ]
    for label, value in meta_rows:
        cells = meta.add_row().cells
        _fill_cell(cells[0], label, bold=True)
        _fill_cell(cells[1], value)

    document.add_paragraph()
    items = row.items_json or []
    table = document.add_table(rows=1, cols=7)
    table.style = "Table Grid"
    headers = ("序号", "名称", "规格说明", "数量", "单位", "单价（元）", "金额（元）")
    for index, header in enumerate(headers):
        _fill_cell(table.rows[0].cells[index], header, bold=True)
    for index, item in enumerate(items, start=1):
        cells = table.add_row().cells
        values = (
            str(index),
            str(item.get("name") or ""),
            str(item.get("spec") or ""),
            str(item.get("quantity") or ""),
            str(item.get("unit") or ""),
            str(item.get("unit_price") or ""),
            str(item.get("amount") or ""),
        )
        for cell, value in zip(cells, values):
            _fill_cell(cell, value)
    total_cells = table.add_row().cells
    _fill_cell(total_cells[0], "合计", bold=True)
    for cell in total_cells[1:6]:
        _fill_cell(cell, "")
    _fill_cell(total_cells[6], money_text(row.total_amount) or "", bold=True)

    words = document.add_paragraph()
    words.paragraph_format.first_line_indent = Pt(0)
    run = words.add_run(f"人民币大写：{row.amount_in_words}")
    run.bold = True
    _set_east_asia(run, "宋体")

    if row.notes:
        notes = document.add_paragraph()
        notes.paragraph_format.first_line_indent = Pt(0)
        run = notes.add_run(f"备注：{row.notes}")
        _set_east_asia(run, "宋体")

    document.add_paragraph()
    for line in (
        f"供应商（盖章）：{row.supplier_name or '＿＿＿＿＿＿＿＿＿＿'}",
        "法定代表人或授权代表（签字）：＿＿＿＿＿＿＿＿＿＿",
        "日期：＿＿＿＿年＿＿月＿＿日",
    ):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.first_line_indent = Pt(0)
        run = paragraph.add_run(line)
        _set_east_asia(run, "宋体")

    document.core_properties.title = f"报价单 {row.quote_number}"
    document.core_properties.author = row.supplier_name or ""
    return document


async def export_quote_docx(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID
) -> tuple[bytes, str]:
    row = await latest_quote(session, tenant_id, project_id)
    project = await get_project(session, tenant_id, project_id)
    document = build_quote_document(row, project.code)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue(), f"quote_{row.quote_number}.docx"