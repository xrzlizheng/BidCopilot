import csv
import io
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.core.spreadsheet import active_sheet
from app.modules.assets.domain import ASSET_KINDS, canonical_row, parse_date, parse_decimal
from app.modules.assets.models import Company
from app.modules.assets.repository import CompanyRepository
from app.modules.assets.schemas import ImportErrorItem, ImportResult
from app.modules.assets.service import upsert_asset

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000


def read_rows(filename: str, content: bytes) -> list[dict[str, Any]]:
    if len(content) > MAX_FILE_BYTES:
        raise ValidationAppError("文件大小超过 10MB 上限")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
        reader = csv.DictReader(io.StringIO(text))
        result: list[dict[str, Any]] = []
        for row in reader:
            if not any(value not in (None, "") for value in row.values()):
                continue
            result.append(dict(row))
            if len(result) > MAX_ROWS:
                raise ValidationAppError(f"数据行数超过 {MAX_ROWS} 行上限")
        return result
    if suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        sheet = active_sheet(workbook)
        rows = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows)
        except StopIteration:
            return []
        headers = [str(header).strip() if header is not None else "" for header in header_row]
        result = []
        # 流式读取并在超限时立即停止：不能先 list() 解压整表再检查行数（zip bomb）。
        for row_values in rows:
            if not any(value is not None for value in row_values):
                continue
            result.append(dict(zip(headers, row_values, strict=False)))
            if len(result) > MAX_ROWS:
                raise ValidationAppError(f"数据行数超过 {MAX_ROWS} 行上限")
        return result
    raise ValidationAppError("仅支持 .csv 与 .xlsx 文件")


def _required(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _asset_values(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "qualifications":
        return {
            "name": _required(data, "name"),
            "level": data.get("level") or None,
            "cert_no": data.get("cert_no") or None,
            "issuer": data.get("issuer") or None,
            "valid_from": parse_date(data.get("valid_from")),
            "valid_to": parse_date(data.get("valid_to")),
        }
    if kind == "personnel":
        return {
            "name": _required(data, "name"),
            "cert_type": _required(data, "cert_type"),
            "level": data.get("level") or None,
            "major": data.get("major") or None,
            "cert_no": data.get("cert_no") or None,
            "valid_to": parse_date(data.get("valid_to")),
        }
    if kind == "performances":
        return {
            "project_name": _required(data, "project_name"),
            "amount": parse_decimal(data.get("amount")),
            "complete_date": parse_date(data.get("complete_date")),
            "buyer": data.get("buyer") or None,
            "category": data.get("category") or None,
        }
    checked = parse_date(data.get("checked_at"))
    return {
        "check_item": _required(data, "check_item"),
        "status": _required(data, "status"),
        "checked_at": datetime.combine(checked, datetime.min.time(), tzinfo=UTC) if checked else None,
    }


async def import_assets(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    kind: str,
    filename: str,
    content: bytes,
) -> ImportResult:
    if kind not in ASSET_KINDS:
        raise NotFoundError()
    rows = read_rows(filename, content)
    if len(rows) > MAX_ROWS:
        raise ValidationAppError("表格数据超过 5000 行上限")

    imported = 0
    errors: list[ImportErrorItem] = []
    for index, raw in enumerate(rows, start=2):
        data = canonical_row(raw)
        try:
            company_name = _required(data, "company_name")
            repo = CompanyRepository(session, tenant_id)
            company = (
                await session.execute(repo.scoped(select(Company).where(Company.name == company_name)))
            ).scalar_one_or_none()
            if company is None:
                company = Company(
                    tenant_id=tenant_id,
                    name=company_name,
                    credit_code=data.get("credit_code") or None,
                    region=data.get("region") or None,
                    industry=data.get("industry") or None,
                    source="import",
                )
                session.add(company)
                await session.flush()
            values = _asset_values(kind, data)
            await upsert_asset(
                session,
                tenant_id=tenant_id,
                kind=kind,
                company_id=company.id,
                values=values,
                source="import",
            )
            imported += 1
        except Exception as exc:
            errors.append(
                ImportErrorItem(
                    row=index,
                    field=None,
                    message=str(exc) or exc.__class__.__name__,
                )
            )
    await session.commit()
    return ImportResult(imported=imported, failed=len(errors), errors=errors)
