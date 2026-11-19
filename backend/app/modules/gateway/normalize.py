import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.html import safe_source_url, sanitize_html
from app.core.region import normalize_region, region_from_text
from app.modules.gateway.contract import DataError

_OBJECT_REF_RE = re.compile(
    r"^[A-Za-z0-9._-]+/[0-9a-fA-F-]{16,}/files/[A-Za-z0-9._-]+\.(pdf|docx?|xlsx?|zip|ofd)$"
)
_ATTACHMENT_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip|ofd)$", re.IGNORECASE)

_INFO_TYPES = {"1": "tender", "2": "planned", "3": "award", "-1": "other"}


def looks_like_object_ref(value: Any) -> bool:
    """上游有时把对象存储 key 放进 content（如 bid-v3-content/<uuid>/files/<uuid>.pdf）。

    它不是公告正文。若当正文入库，会污染正文展示，还会让依赖正文的解析
    （资质要求抽取等）读到一串存储路径。此类情况标记为「附件原文」并留空正文。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 512:
        return False
    if "<" in text and ">" in text:
        return False
    if _OBJECT_REF_RE.match(text):
        return True
    return bool(
        "/" in text
        and not any(ch.isspace() for ch in text)
        and _ATTACHMENT_EXT_RE.search(text)
    )


@dataclass
class NormalizedAward:
    dedup_key: str
    project_code: str | None
    project_name: str | None
    winner: str
    win_amount: Decimal | None
    award_at: datetime | None
    source: str
    source_url: str | None
    fetched_at: datetime


@dataclass
class NormalizedOpportunity:
    source: str
    source_id: str | None
    dedup_key: str
    title: str
    project_code: str | None
    project_name: str | None
    buyer: str | None
    agency: str | None
    winner: str | None
    region: str | None
    industry: list[str]
    budget_amount: Decimal | None
    win_bid_amount: Decimal | None
    publish_at: datetime | None
    info_type: str
    source_url: str | None
    detail_status: str
    confidence: str
    content_html: str | None
    content_text: str | None
    content_fetched_at: datetime | None
    fetched_at: datetime
    awards: list[NormalizedAward] = field(default_factory=list)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = " ".join(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return text or None


def parse_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).replace(",", "").replace("，", "")
    tokens = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not tokens:
        return None
    return max(Decimal(token) for token in tokens)


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _canonical_iso(value: datetime | None) -> str:
    return value.astimezone(UTC).isoformat() if value is not None else ""


def opportunity_fallback_key(
    *,
    title: str,
    buyer: str | None,
    publish_at: datetime | None,
    info_type: str,
    project_code: str | None,
) -> str:
    """uid 缺失时的兜底身份：类型与项目编号参与，避免招标/中标或不同包件互相覆盖。"""
    return _dedup_key(
        title,
        buyer or "",
        _canonical_iso(publish_at),
        info_type,
        project_code or "",
    )


def award_fallback_key(
    *,
    title: str,
    project_code: str | None,
    project_name: str | None,
    winner: str,
    award_at: datetime | None,
) -> str:
    return _dedup_key(
        project_code or "",
        project_name or "",
        title,
        winner,
        _canonical_iso(award_at),
    )


def normalize_opportunity(
    raw: Any,
    *,
    fetched_at: datetime,
    include_content: bool = False,
) -> tuple[NormalizedOpportunity | None, list[DataError]]:
    warnings: list[DataError] = []
    if not isinstance(raw, dict):
        return None, [DataError(code="invalid_item", message="opportunity item is not an object")]

    title = clean_text(raw.get("title"))
    if not title:
        return None, [DataError(code="missing_title", message="title is required", field="title")]

    source_id = clean_text(raw.get("uid"))
    buyer = clean_text(raw.get("bidEntInfos"))
    winner = clean_text(raw.get("winEidInfos"))
    publish_at = parse_datetime(raw.get("publishDate"))
    project_code = clean_text(raw.get("projectNo"))
    project_name = clean_text(raw.get("projectName"))
    info_type = _INFO_TYPES.get(str(raw.get("infoType", "")), "other")
    if source_id is None:
        warnings.append(
            DataError(
                code="missing_uid",
                message="uid is missing; dedup falls back to title+buyer+publish_at+type+project_code",
                field="uid",
            )
        )
    key = source_id or opportunity_fallback_key(
        title=title,
        buyer=buyer,
        publish_at=publish_at,
        info_type=info_type,
        project_code=project_code,
    )
    # 上游常只给市或区县，甚至全省略。先按原样拼，再用标题/采购人补全，
    # 修掉「北京 丰台区 丰台区」这类重复并补上缺失的省份——
    # 地区筛选用 ilike，只写「合肥市」的公告搜「安徽」是搜不到的。
    raw_region = " ".join(
        part
        for part in (
            clean_text(raw.get("province")),
            clean_text(raw.get("city")),
            clean_text(raw.get("county")),
        )
        if part
    ) or None
    region = normalize_region(raw_region) or region_from_text(title, buyer) or raw_region
    industry_raw = raw.get("industry")
    industry = [str(item) for item in industry_raw] if isinstance(industry_raw, list) else []
    content_html = content_text = None
    content_fetched_at = None
    detail_status = "list_only"
    if include_content:
        raw_content = raw.get("content")
        if looks_like_object_ref(raw_content):
            # 正文实际是附件（对象存储 key），不落正文；交由前端引导打开原文
            detail_status = "attachment"
            warnings.append(
                DataError(
                    code="content_attachment_only",
                    message="公告正文为附件文件，未解析正文，请打开原文查看",
                    field="content",
                )
            )
        else:
            content_html, content_text = sanitize_html(raw_content)
            if content_html:
                detail_status = "full"
                content_fetched_at = fetched_at

    awards: list[NormalizedAward] = []
    award_at = publish_at if info_type == "award" else None
    win_amount = parse_amount(raw.get("maxWinBidAmount")) or parse_amount(raw.get("winBidAmount"))
    if info_type == "award" and winner:
        # uid 在位时保持原键不变（避免存量 award 行重复）；缺失时才启用硬化兜底。
        award_key = (
            _dedup_key(
                project_code or "",
                winner,
                award_at.isoformat() if award_at else "",
                source_id,
            )
            if source_id
            else award_fallback_key(
                title=title,
                project_code=project_code,
                project_name=project_name,
                winner=winner,
                award_at=award_at,
            )
        )
        awards.append(
            NormalizedAward(
                dedup_key=award_key,
                project_code=project_code,
                project_name=project_name,
                winner=winner,
                win_amount=win_amount,
                award_at=award_at,
                source="keeprisk:E_007_1271",
                source_url=safe_source_url(raw.get("sourceUrl")),
                fetched_at=fetched_at,
            )
        )

    return (
        NormalizedOpportunity(
            source="keeprisk:E_007_1271",
            source_id=source_id,
            dedup_key=key,
            title=title,
            project_code=project_code,
            project_name=clean_text(raw.get("projectName")),
            buyer=buyer,
            agency=clean_text(raw.get("agentEntInfos")),
            winner=winner,
            region=region,
            industry=industry,
            budget_amount=parse_amount(raw.get("maxBudgetAmount")) or parse_amount(raw.get("budgetAmount")),
            win_bid_amount=win_amount,
            publish_at=publish_at,
            info_type=info_type,
            source_url=safe_source_url(raw.get("sourceUrl")),
            detail_status=detail_status,
            confidence="high" if source_id and publish_at else "medium",
            content_html=content_html,
            content_text=content_text,
            content_fetched_at=content_fetched_at,
            fetched_at=fetched_at,
            awards=awards,
        ),
        warnings,
    )


def normalize_page(
    data: Any,
    *,
    fetched_at: datetime,
    include_content: bool = False,
) -> tuple[list[NormalizedOpportunity], list[DataError]]:
    if isinstance(data, dict):
        raw_items = data.get("list") or []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    items: list[NormalizedOpportunity] = []
    warnings: list[DataError] = []
    for raw in raw_items:
        item, item_warnings = normalize_opportunity(
            raw, fetched_at=fetched_at, include_content=include_content
        )
        warnings.extend(item_warnings)
        if item is not None:
            items.append(item)
    return items, warnings
