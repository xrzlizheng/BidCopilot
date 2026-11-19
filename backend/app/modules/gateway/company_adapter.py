from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.modules.gateway.contract import (
    DataError,
    DataResult,
    SourceStatus,
    status_for_gateway_code,
)
from app.modules.gateway.keeprisk_client import (
    GatewayEnvelope,
    KeepRiskClient,
    KeepRiskConfigError,
    KeepRiskResponseError,
    KeepRiskTransportError,
)
from app.modules.gateway.normalize import (
    NormalizedOpportunity,
    clean_text,
    normalize_page,
    parse_datetime,
)

PROFILE_SOURCE = "keeprisk:E_007_1341"
BIDS_SOURCE = "keeprisk:E_007_1271"

ROLE_SEARCH_TYPES = {"tenderer": "3", "winner": "4", "agency": "5"}
ROLE_INFO_TYPES = {"winner": ["3"]}
ROLE_LABELS = {
    "1": "tenderer",
    "2": "winner",
    "3": "agency",
    "4": "other",
    "5": "bidder",
}


@dataclass(frozen=True, slots=True)
class CompanyProfileEntry:
    subject_name: str
    subject_status: str | None
    subject_type: str | None
    region: str | None
    roles: list[str]
    related_count: int
    bidder_count: int
    winner_count: int
    agent_count: int
    tender_count: int
    latest_publish_at: datetime | None
    latest_title: str | None
    latest_role: str | None
    info_type: str | None


@dataclass(frozen=True, slots=True)
class CompanyProfilePage:
    total: int
    entries: list[CompanyProfileEntry] = field(default_factory=list)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _roles(value: Any) -> list[str]:
    raw: list[Any]
    if isinstance(value, list):
        raw = value
    elif value is None or value == "":
        raw = []
    else:
        raw = [part for part in str(value).replace("，", ",").split(",") if part.strip()]
    roles: list[str] = []
    for item in raw:
        code = str(item).strip()
        if not code:
            continue
        mapped = ROLE_LABELS.get(code, code)
        if mapped not in roles:
            roles.append(mapped)
    return roles


def _region(raw: dict[str, Any]) -> str | None:
    parts = [clean_text(raw.get(key)) for key in ("province", "city", "county")]
    return " ".join(part for part in parts if part) or None


def normalize_profile(
    raw: Any, *, fetched_at: datetime
) -> tuple[CompanyProfileEntry | None, list[DataError]]:
    if not isinstance(raw, dict):
        return None, [
            DataError(code="invalid_item", message="company profile item is not an object")
        ]
    subject_name = clean_text(raw.get("subjectName"))
    if not subject_name:
        return None, [
            DataError(
                code="missing_subject_name",
                message="subjectName is required",
                field="subjectName",
            )
        ]
    entry = CompanyProfileEntry(
        subject_name=subject_name,
        subject_status=clean_text(raw.get("subjectStatus")),
        subject_type=clean_text(raw.get("subjectType")),
        region=_region(raw),
        roles=_roles(raw.get("roles")),
        related_count=_to_int(raw.get("relatedCount")),
        bidder_count=_to_int(raw.get("bidderCount")),
        winner_count=_to_int(raw.get("winnerCount")),
        agent_count=_to_int(raw.get("agentCount")),
        tender_count=_to_int(raw.get("tenderCount")),
        latest_publish_at=parse_datetime(raw.get("publishDate")),
        latest_title=clean_text(raw.get("title")),
        latest_role=clean_text(raw.get("role")),
        info_type=clean_text(raw.get("infoType")),
    )
    return entry, []


def normalize_profile_page(
    data: Any, *, fetched_at: datetime
) -> tuple[CompanyProfilePage, list[DataError]]:
    if isinstance(data, dict):
        raw_items = data.get("list") or []
        total = _to_int(data.get("total")) or len(raw_items)
    elif isinstance(data, list):
        raw_items = data
        total = len(raw_items)
    else:
        return CompanyProfilePage(total=0), [
            DataError(code="invalid_payload", message="company profile data is not an object")
        ]
    entries: list[CompanyProfileEntry] = []
    warnings: list[DataError] = []
    for raw in raw_items:
        entry, item_warnings = normalize_profile(raw, fetched_at=fetched_at)
        warnings.extend(item_warnings)
        if entry is not None:
            entries.append(entry)
    return CompanyProfilePage(total=total, entries=entries), warnings


class KeepRiskCompanyIntelAdapter:
    def __init__(self, client: KeepRiskClient) -> None:
        self.client = client

    async def fetch_profile(self, company_name: str) -> DataResult[CompanyProfilePage]:
        fetched_at = datetime.now(UTC)
        body = {
            "entName": {"eq": company_name, "type": "1"},
            "pageNo": "1",
            "pageSize": "20",
        }
        try:
            envelope = await self.client.query("E_007_1341", body)
        except KeepRiskConfigError as exc:
            return self._failure(
                PROFILE_SOURCE, SourceStatus.UNAVAILABLE, "config_missing", str(exc), fetched_at
            )
        except KeepRiskTransportError as exc:
            return self._failure(
                PROFILE_SOURCE, SourceStatus.UNAVAILABLE, "transport_error", str(exc), fetched_at
            )
        except KeepRiskResponseError as exc:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=PROFILE_SOURCE,
                fetched_at=fetched_at,
                error=DataError(
                    code="invalid_response",
                    message=str(exc),
                    raw_payload_ref=exc.raw_ref,
                ),
                raw_ref=exc.raw_ref,
            )
        return self._normalize_profile_envelope(envelope)

    async def fetch_bids(
        self,
        company_name: str,
        *,
        role: str,
        page_size: int = 50,
        info_types: list[str] | None = None,
    ) -> DataResult[list[NormalizedOpportunity]]:
        if role not in ROLE_SEARCH_TYPES:
            raise ValueError(f"unsupported company role: {role}")
        fetched_at = datetime.now(UTC)
        body: dict[str, Any] = {
            "pageNo": "1",
            "pageSize": str(min(page_size, 50)),
            "contentFlag": "0",
            "searchKey": {"type": ROLE_SEARCH_TYPES[role], "text": company_name},
        }
        requested_types = info_types if info_types is not None else ROLE_INFO_TYPES.get(role)
        if requested_types:
            body["InfoTypeList"] = requested_types
        try:
            envelope = await self.client.query("E_007_1271", body)
        except KeepRiskConfigError as exc:
            return self._failure(
                BIDS_SOURCE, SourceStatus.UNAVAILABLE, "config_missing", str(exc), fetched_at
            )
        except KeepRiskTransportError as exc:
            return self._failure(
                BIDS_SOURCE, SourceStatus.UNAVAILABLE, "transport_error", str(exc), fetched_at
            )
        except KeepRiskResponseError as exc:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=BIDS_SOURCE,
                fetched_at=fetched_at,
                error=DataError(
                    code="invalid_response",
                    message=str(exc),
                    raw_payload_ref=exc.raw_ref,
                ),
                raw_ref=exc.raw_ref,
            )
        return self._normalize_bids_envelope(envelope)

    def _failure(
        self,
        source: str,
        status: SourceStatus,
        code: str,
        message: str,
        fetched_at: datetime,
    ) -> DataResult[Any]:
        return DataResult(
            status=status,
            data=None,
            source=source,
            fetched_at=fetched_at,
            error=DataError(code=code, message=message),
        )

    def _normalize_profile_envelope(
        self, envelope: GatewayEnvelope
    ) -> DataResult[CompanyProfilePage]:
        status = status_for_gateway_code(envelope.code)
        if status not in {SourceStatus.OK, SourceStatus.EMPTY}:
            return DataResult(
                status=status,
                data=None,
                source=PROFILE_SOURCE,
                fetched_at=envelope.fetched_at,
                error=DataError(
                    code=f"gateway_{envelope.code}",
                    message=envelope.message,
                    raw_payload_ref=envelope.raw_ref,
                ),
                raw_ref=envelope.raw_ref,
                order_no=envelope.order_no,
                charged=envelope.charge,
            )
        page, warnings = normalize_profile_page(envelope.data, fetched_at=envelope.fetched_at)
        raw_items = envelope.data.get("list") if isinstance(envelope.data, dict) else envelope.data
        if raw_items and not page.entries:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=PROFILE_SOURCE,
                fetched_at=envelope.fetched_at,
                error=DataError(
                    code="no_normalizable_items",
                    message="gateway returned items but none were normalizable",
                    raw_payload_ref=envelope.raw_ref,
                ),
                warnings=warnings,
                raw_ref=envelope.raw_ref,
                order_no=envelope.order_no,
                charged=envelope.charge,
            )
        return DataResult(
            status=SourceStatus.OK if page.entries else SourceStatus.EMPTY,
            data=page,
            source=PROFILE_SOURCE,
            fetched_at=envelope.fetched_at,
            warnings=warnings,
            raw_ref=envelope.raw_ref,
            order_no=envelope.order_no,
            charged=envelope.charge,
        )

    def _normalize_bids_envelope(
        self, envelope: GatewayEnvelope
    ) -> DataResult[list[NormalizedOpportunity]]:
        status = status_for_gateway_code(envelope.code)
        if status not in {SourceStatus.OK, SourceStatus.EMPTY}:
            return DataResult(
                status=status,
                data=None,
                source=BIDS_SOURCE,
                fetched_at=envelope.fetched_at,
                error=DataError(
                    code=f"gateway_{envelope.code}",
                    message=envelope.message,
                    raw_payload_ref=envelope.raw_ref,
                ),
                raw_ref=envelope.raw_ref,
                order_no=envelope.order_no,
                charged=envelope.charge,
            )
        items, warnings = normalize_page(
            envelope.data, fetched_at=envelope.fetched_at, include_content=False
        )
        raw_items = envelope.data.get("list") if isinstance(envelope.data, dict) else envelope.data
        if raw_items and not items:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=BIDS_SOURCE,
                fetched_at=envelope.fetched_at,
                error=DataError(
                    code="no_normalizable_items",
                    message="gateway returned items but none were normalizable",
                    raw_payload_ref=envelope.raw_ref,
                ),
                warnings=warnings,
                raw_ref=envelope.raw_ref,
                order_no=envelope.order_no,
                charged=envelope.charge,
            )
        return DataResult(
            status=SourceStatus.OK if items else SourceStatus.EMPTY,
            data=items,
            source=BIDS_SOURCE,
            fetched_at=envelope.fetched_at,
            warnings=warnings,
            raw_ref=envelope.raw_ref,
            order_no=envelope.order_no,
            charged=envelope.charge,
        )
