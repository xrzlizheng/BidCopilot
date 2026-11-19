from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
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
from app.modules.gateway.normalize import NormalizedOpportunity, normalize_page

SOURCE = "keeprisk:E_007_1271"


@dataclass(frozen=True)
class OpportunityQuery:
    keywords: list[str] = field(default_factory=list)
    info_types: list[str] = field(default_factory=list)
    publish_from: date | None = None
    publish_to: date | None = None
    region_list: list[dict[str, str]] | None = None
    industry_list: list[str] | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    search_key: dict[str, Any] | None = None
    page_size: int = 50

    def to_json(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "info_types": self.info_types,
            "publish_from": self.publish_from.isoformat() if self.publish_from else None,
            "publish_to": self.publish_to.isoformat() if self.publish_to else None,
            "region_list": self.region_list,
            "industry_list": self.industry_list,
            "budget_min": str(self.budget_min) if self.budget_min is not None else None,
            "budget_max": str(self.budget_max) if self.budget_max is not None else None,
            "search_key": self.search_key,
            "page_size": self.page_size,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "OpportunityQuery":
        def _date(value: Any) -> date | None:
            return date.fromisoformat(value) if value else None

        def _decimal(value: Any) -> Decimal | None:
            return Decimal(str(value)) if value is not None and value != "" else None

        return cls(
            keywords=list(data.get("keywords") or []),
            info_types=list(data.get("info_types") or []),
            publish_from=_date(data.get("publish_from")),
            publish_to=_date(data.get("publish_to")),
            region_list=data.get("region_list"),
            industry_list=data.get("industry_list"),
            budget_min=_decimal(data.get("budget_min")),
            budget_max=_decimal(data.get("budget_max")),
            search_key=data.get("search_key"),
            page_size=int(data.get("page_size") or 50),
        )


class KeepRiskTenderAdapter:
    def __init__(self, client: KeepRiskClient) -> None:
        self.client = client

    def _body(self, query: OpportunityQuery, page_no: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "pageNo": str(page_no),
            "pageSize": str(min(query.page_size, 50)),
            "contentFlag": "0",
        }
        if query.keywords:
            body["keywords"] = query.keywords
        if query.info_types:
            body["InfoTypeList"] = query.info_types
        if query.publish_from and query.publish_to:
            body["publishTime"] = {
                "from": query.publish_from.isoformat(),
                "to": query.publish_to.isoformat(),
            }
        if query.region_list:
            body["regionList"] = query.region_list
        if query.industry_list:
            body["industryList"] = query.industry_list
        if query.budget_min is not None or query.budget_max is not None:
            body["budgetAmountList"] = [
                {
                    "from": str(query.budget_min) if query.budget_min is not None else "",
                    "to": str(query.budget_max) if query.budget_max is not None else "",
                }
            ]
        if query.search_key:
            body["searchKey"] = query.search_key
        return body

    async def fetch_page(
        self,
        query: OpportunityQuery,
        *,
        page_no: int = 1,
    ) -> DataResult[list[NormalizedOpportunity]]:
        return await self._fetch(self._body(query, page_no), include_content=False)

    async def fetch_detail(self, ids: list[str]) -> DataResult[list[NormalizedOpportunity]]:
        if not ids:
            return DataResult(
                status=SourceStatus.EMPTY,
                data=[],
                source=SOURCE,
                fetched_at=datetime.now(UTC),
            )
        body = {
            "ids": ids,
            "contentFlag": "1",
            "pageNo": "1",
            "pageSize": str(len(ids)),
        }
        return await self._fetch(body, include_content=True)

    async def _fetch(
        self,
        body: dict[str, Any],
        *,
        include_content: bool,
    ) -> DataResult[list[NormalizedOpportunity]]:
        fetched_at = datetime.now(UTC)
        try:
            envelope = await self.client.query("E_007_1271", body)
        except KeepRiskConfigError as exc:
            return DataResult(
                status=SourceStatus.UNAVAILABLE,
                data=None,
                source=SOURCE,
                fetched_at=fetched_at,
                error=DataError(code="config_missing", message=str(exc)),
            )
        except KeepRiskTransportError as exc:
            return DataResult(
                status=SourceStatus.UNAVAILABLE,
                data=None,
                source=SOURCE,
                fetched_at=fetched_at,
                error=DataError(code="transport_error", message=str(exc)),
            )
        except KeepRiskResponseError as exc:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=SOURCE,
                fetched_at=fetched_at,
                error=DataError(
                    code="invalid_response",
                    message=str(exc),
                    raw_payload_ref=exc.raw_ref,
                ),
                raw_ref=exc.raw_ref,
            )
        return self._normalize_envelope(envelope, include_content=include_content)

    def _normalize_envelope(
        self,
        envelope: GatewayEnvelope,
        *,
        include_content: bool,
    ) -> DataResult[list[NormalizedOpportunity]]:
        status = status_for_gateway_code(envelope.code)
        if status not in {SourceStatus.OK, SourceStatus.EMPTY}:
            return DataResult(
                status=status,
                data=None,
                source=SOURCE,
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
            envelope.data,
            fetched_at=envelope.fetched_at,
            include_content=include_content,
        )
        raw_items = envelope.data.get("list") if isinstance(envelope.data, dict) else envelope.data
        if raw_items and not items:
            return DataResult(
                status=SourceStatus.CONTRACT_ERROR,
                data=None,
                source=SOURCE,
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
            source=SOURCE,
            fetched_at=envelope.fetched_at,
            source_url=items[0].source_url if items else None,
            warnings=warnings,
            raw_ref=envelope.raw_ref,
            order_no=envelope.order_no,
            charged=envelope.charge,
        )
