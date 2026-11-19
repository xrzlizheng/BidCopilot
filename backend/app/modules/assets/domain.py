import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.assets.models import Credit, Performance, Personnel, Qualification

ASSET_KINDS: dict[str, Any] = {
    "qualifications": Qualification,
    "personnel": Personnel,
    "performances": Performance,
    "credits": Credit,
}

HEADER_ALIASES = {
    "company_name": {"company_name", "企业名称", "公司名称"},
    "credit_code": {"credit_code", "统一社会信用代码", "信用代码"},
    "name": {"name", "名称", "姓名"},
    "level": {"level", "等级"},
    "cert_no": {"cert_no", "证书编号", "证书号"},
    "issuer": {"issuer", "发证机关", "颁发机构"},
    "valid_from": {"valid_from", "生效日期", "起始日期"},
    "valid_to": {"valid_to", "到期日期", "有效期至"},
    "cert_type": {"cert_type", "证书类型"},
    "major": {"major", "专业"},
    "project_name": {"project_name", "项目名称"},
    "amount": {"amount", "合同金额", "金额"},
    "complete_date": {"complete_date", "完成日期"},
    "buyer": {"buyer", "采购人", "业主"},
    "category": {"category", "类别"},
    "check_item": {"check_item", "检查项"},
    "status": {"status", "状态"},
    "checked_at": {"checked_at", "检查时间", "检查日期"},
    "region": {"region", "地区"},
    "industry": {"industry", "行业"},
}


def canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        header = str(key).strip()
        for canonical, aliases in HEADER_ALIASES.items():
            if header in aliases:
                normalized[canonical] = value
                break
    return normalized


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return (datetime(1899, 12, 30, tzinfo=UTC) + timedelta(days=float(value))).date()
    text = str(value).strip().replace("/", "-")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "").replace("，", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("金额必须是数字（单位：元）") from exc


def asset_dedup_key(kind: str, company_id: Any, values: dict[str, Any]) -> str:
    if kind == "qualifications":
        parts = [values.get("name"), values.get("level"), values.get("cert_no")]
    elif kind == "personnel":
        parts = [values.get("name"), values.get("cert_type"), values.get("cert_no")]
    elif kind == "performances":
        parts = [values.get("project_name"), values.get("complete_date"), values.get("amount")]
    else:
        parts = [values.get("check_item"), values.get("checked_at")]
    payload = json.dumps(
        [
            kind,
            str(company_id),
            str(values.get("org_unit_id") or ""),
            *[str(part) if part is not None else "" for part in parts],
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
