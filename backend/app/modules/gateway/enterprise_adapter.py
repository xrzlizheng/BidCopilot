import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.modules.gateway.contract import (
    DataError,
    DataResult,
    SourceStatus,
    status_for_gateway_code,
)
from app.modules.gateway.keeprisk_mcp import (
    KeepRiskMcpClient,
    McpConfigError,
    McpResponseError,
    McpToolError,
    McpToolResult,
    McpTransportError,
)

logger = logging.getLogger(__name__)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


TOOL_CALL_UNITS: dict[str, int] = {
    "resolve_entity": 1,
    "enterprise_basic_profile": 1,
    "enterprise_credit_score": 6,
    "enterprise_graph_relations": 4,
    "enterprise_due_diligence_snapshot": 26,
    "batch_query_companies": 1,
    "compare_enterprises": 10,
}


class EnterpriseIntelAdapter:
    source = "keeprisk-mcp"

    def __init__(self, client: KeepRiskMcpClient) -> None:
        self.client = client

    @staticmethod
    def estimate_units(tool: str, *, sections: str | None = None) -> int:
        if tool == "enterprise_due_diligence_snapshot" and sections:
            count = len([item for item in sections.split(",") if item.strip()])
            if count:
                return count
        return TOOL_CALL_UNITS.get(tool, 1)

    async def resolve(self, keyword: str, *, region: str | None = None) -> DataResult[dict[str, Any]]:
        arguments: dict[str, Any] = {"keyword": keyword}
        if region:
            arguments["region"] = region
        return await self._call("resolve_entity", arguments)

    async def basic_profile(self, ent_name: str) -> DataResult[dict[str, Any]]:
        return await self._call("enterprise_basic_profile", {"ent_name": ent_name})

    async def batch_verify(self, ent_names: list[str]) -> DataResult[dict[str, Any]]:
        return await self._call("batch_query_companies", {"ent_names": ent_names})

    async def compare(self, ent_names: list[str]) -> DataResult[dict[str, Any]]:
        result = await self._call("compare_enterprises", {"ent_names": ent_names})
        if not result.is_success or not isinstance(result.data, dict):
            return result
        companies = result.data.get("companies") or []
        calls = 0
        for company in companies:
            if isinstance(company, dict):
                credit = company.get("credit_score") or {}
                if isinstance(credit, dict):
                    calls += _as_int(credit.get("api_calls"))
        if calls and not result.api_calls:
            result = replace(
                result,
                api_calls=calls,
                billing_units=result.billing_units or calls,
                charged=True,
            )
        return result

    async def credit_score(self, ent_name: str) -> DataResult[dict[str, Any]]:
        return await self._call("enterprise_credit_score", {"ent_name": ent_name})

    async def graph_relations(
        self, ent_name: str, *, digest_only: bool = True
    ) -> DataResult[dict[str, Any]]:
        return await self._call(
            "enterprise_graph_relations",
            {"ent_name": ent_name, "digest_only": digest_only},
        )

    async def due_diligence(
        self,
        ent_name: str,
        *,
        sections: str | None = None,
        include_judicial: bool = True,
        digest_only: bool = True,
    ) -> DataResult[dict[str, Any]]:
        arguments: dict[str, Any] = {
            "ent_name": ent_name,
            "include_judicial": include_judicial,
            "digest_only": digest_only,
        }
        if sections:
            arguments["sections"] = sections
        return await self._call("enterprise_due_diligence_snapshot", arguments)

    async def _call(self, tool: str, arguments: dict[str, Any]) -> DataResult[dict[str, Any]]:
        try:
            result = await self.client.call_tool(tool, arguments)
        except McpConfigError as exc:
            return self._error(SourceStatus.UNAVAILABLE, "mcp_not_configured", str(exc))
        except McpTransportError as exc:
            if exc.status_code in {401, 403}:
                status = SourceStatus.UNAUTHORIZED
            elif exc.status_code == 429:
                status = SourceStatus.RATE_LIMITED
            else:
                status = SourceStatus.UNAVAILABLE
            return self._error(status, f"http_{exc.status_code or 'error'}", str(exc))
        except McpToolError as exc:
            message = str(exc)
            lowered = message.lower()
            if "unauthorized" in lowered or "401" in lowered:
                status = SourceStatus.UNAUTHORIZED
            elif "quota" in lowered or "429" in lowered or "rate" in lowered:
                status = SourceStatus.RATE_LIMITED
            else:
                status = SourceStatus.CONTRACT_ERROR
            return self._error(status, "mcp_tool_error", message, raw_ref=exc.raw_ref)
        except McpResponseError as exc:
            return self._error(SourceStatus.CONTRACT_ERROR, "mcp_response_error", str(exc), raw_ref=exc.raw_ref)
        return self._map_result(result)

    def _error(
        self,
        status: SourceStatus,
        code: str,
        message: str,
        *,
        raw_ref: str | None = None,
    ) -> DataResult[dict[str, Any]]:
        logger.info("keeprisk mcp error status=%s code=%s", status.value, code)
        return DataResult(
            status=status,
            data=None,
            source=self.source,
            fetched_at=datetime.now(UTC),
            source_url=self.client.url,
            error=DataError(code=code, message=message, raw_payload_ref=raw_ref),
            raw_ref=raw_ref,
        )

    def _map_result(self, result: McpToolResult) -> DataResult[dict[str, Any]]:
        payload = result.payload
        status = SourceStatus.OK
        data: Any = payload
        error: DataError | None = None
        warnings: list[DataError] = []

        if "code" in payload:
            code = str(payload.get("code"))
            status = status_for_gateway_code(code)
            data = payload.get("data")
            if status not in {SourceStatus.OK, SourceStatus.EMPTY}:
                error = DataError(
                    code=code,
                    message=str(payload.get("message") or ""),
                    raw_payload_ref=result.raw_ref,
                )
        elif "candidates" in payload:
            status = SourceStatus.OK if payload.get("candidates") else SourceStatus.EMPTY
        elif "sections" in payload:
            sections = payload.get("sections") or []
            ok_sections = [item for item in sections if item.get("status") == "ok"]
            failed_sections = [item for item in sections if item.get("status") not in {"ok", None}]
            if sections and not ok_sections and failed_sections:
                status = SourceStatus.CONTRACT_ERROR
                error = DataError(
                    code="all_sections_failed",
                    message="all requested sections failed",
                    raw_payload_ref=result.raw_ref,
                )
            for item in failed_sections:
                warnings.append(
                    DataError(
                        code=str(item.get("key") or "section"),
                        message=f"{item.get('title') or ''}: {item.get('status')}",
                        raw_payload_ref=result.raw_ref,
                    )
                )

        return DataResult(
            status=status,
            data=data,
            source=self.source,
            fetched_at=result.fetched_at,
            source_url=self.client.url,
            error=error,
            warnings=warnings,
            raw_ref=result.raw_ref,
            order_no=result.order_no,
            charged=result.charged,
            api_calls=result.api_calls or (1 if "code" in payload else 0),
            billing_units=result.billing_units or (1 if payload.get("charge") else 0),
        )
