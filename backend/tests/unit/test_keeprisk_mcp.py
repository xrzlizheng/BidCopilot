import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.modules.gateway.contract import SourceStatus
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.gateway.keeprisk_mcp import (
    KeepRiskMcpClient,
    McpToolError,
    McpToolResult,
)


class MemorySnapshotStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    async def put_bytes(self, key: str, payload: bytes, content_type: str = "application/json") -> str:
        self.items[key] = payload
        return key

    async def get_bytes(self, ref: str) -> bytes:
        return self.items[ref]

    async def delete_bytes(self, ref: str) -> None:
        self.items.pop(ref, None)


def _sse(payload: dict[str, Any]) -> bytes:
    data = json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload}, ensure_ascii=False)
    return f"event: message\ndata: {data}\n\n".encode()


def _transport(
    calls: list[dict[str, Any]],
    *,
    tool_payload: dict[str, Any] | None = None,
    is_error: bool = False,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "id": 0, "result": {}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        text = json.dumps(tool_payload or {}, ensure_ascii=False)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"content": [{"type": "text", "text": text}], "isError": is_error}
            ),
        )

    return httpx.MockTransport(handler)


def _client(
    calls: list[dict[str, Any]],
    *,
    tool_payload: dict[str, Any] | None = None,
    is_error: bool = False,
    token: str | None = "token",
) -> KeepRiskMcpClient:
    return KeepRiskMcpClient(
        url="https://mcp.example/mcp",
        token=token,
        timeout_seconds=5,
        snapshot_store=MemorySnapshotStore(),
        transport=_transport(calls, tool_payload=tool_payload, is_error=is_error),
    )


async def test_call_tool_parses_payload_and_reuses_session() -> None:
    calls: list[dict[str, Any]] = []
    payload = {
        "entity": "示例科技有限公司",
        "api_calls": 4,
        "billing_units": 2,
        "orderNo": "order-1",
        "sections": [{"key": "basic", "status": "ok"}],
    }
    client = _client(calls, tool_payload=payload)

    first = await client.call_tool("enterprise_graph_relations", {"ent_name": "示例科技有限公司"})
    second = await client.call_tool("enterprise_basic_profile", {"ent_name": "示例科技有限公司"})
    assert first.payload["entity"] == "示例科技有限公司"
    assert first.api_calls == 4
    assert first.billing_units == 2
    assert first.charged is True
    assert first.order_no == "order-1"
    assert second.tool == "enterprise_basic_profile"
    assert [call["method"] for call in calls].count("initialize") == 1
    assert calls[-1]["params"]["name"] == "enterprise_basic_profile"


async def test_call_tool_raises_on_tool_error() -> None:
    calls: list[dict[str, Any]] = []
    client = _client(calls, tool_payload={"message": "upstream failed"}, is_error=True)

    with pytest.raises(McpToolError, match="upstream failed"):
        await client.call_tool("enterprise_basic_profile", {"ent_name": "示例科技有限公司"})


async def test_adapter_maps_gateway_empty_code() -> None:
    client = _client([], tool_payload={"code": "110", "message": "无记录", "data": None})
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.basic_profile("示例科技有限公司")
    assert result.status is SourceStatus.EMPTY
    assert result.error is None


async def test_adapter_maps_failed_sections_to_contract_error() -> None:
    payload = {
        "sections": [
            {"key": "basic", "title": "工商基本信息", "status": "http_500"},
            {"key": "shareholders", "title": "股东", "status": "timeout"},
        ]
    }
    client = _client([], tool_payload=payload)
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.due_diligence("示例科技有限公司", sections="basic,shareholders")
    assert result.status is SourceStatus.CONTRACT_ERROR
    assert result.error is not None
    assert len(result.warnings) == 2


async def test_adapter_keeps_partial_sections_as_ok_with_warnings() -> None:
    payload = {
        "sections": [
            {"key": "basic", "title": "工商基本信息", "status": "ok"},
            {"key": "shareholders", "title": "股东", "status": "timeout"},
        ]
    }
    client = _client([], tool_payload=payload)
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.due_diligence("示例科技有限公司", sections="basic,shareholders")
    assert result.status is SourceStatus.OK
    assert len(result.warnings) == 1


async def test_adapter_returns_unavailable_when_token_missing() -> None:
    client = _client([], tool_payload={}, token=None)
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.basic_profile("示例科技有限公司")
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "mcp_not_configured"


def test_watchlist_digest_marks_no_records_unknown() -> None:
    from app.modules.company_intel.watchlist_service import _digest

    digest = _digest(
        {
            "sections": [
                {
                    "key": "enforced",
                    "status": "ok",
                    "digest": {"has_records": False, "risk_level": "green", "count": 0},
                }
            ],
            "risk_summary": {"overall_level": "green"},
        }
    )
    assert digest["_risk"] == "unknown"


def test_estimate_units_uses_section_count() -> None:
    assert EnterpriseIntelAdapter.estimate_units(
        "enterprise_due_diligence_snapshot", sections="basic,shareholders,beneficial_owner"
    ) == 3
    assert EnterpriseIntelAdapter.estimate_units("enterprise_graph_relations") == 4
    assert EnterpriseIntelAdapter.estimate_units("batch_query_companies") == 1


async def test_adapter_maps_batch_and_gateway_charge() -> None:
    payload = {
        "code": "100",
        "message": "查询成功",
        "data": [{"entName": "示例科技有限公司", "regStatus": "存续"}],
        "orderNo": "order-batch",
        "charge": True,
    }
    client = _client([], tool_payload=payload)
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.batch_verify(["示例科技有限公司", "未匹配企业"])
    assert result.status is SourceStatus.OK
    assert result.data == [{"entName": "示例科技有限公司", "regStatus": "存续"}]
    assert result.charged is True
    assert result.order_no == "order-batch"
    assert result.api_calls == 1
    assert result.billing_units == 1


async def test_adapter_maps_compare_and_counts_calls() -> None:
    payload = {
        "companies": [
            {"entity": "A", "credit_score": {"api_calls": 6}},
            {"entity": "B", "credit_score": {"api_calls": 6}},
        ],
        "ranking": [{"entity": "B", "rank": 1, "score": 90}],
        "count": 2,
    }
    client = _client([], tool_payload=payload)
    adapter = EnterpriseIntelAdapter(client)
    result = await adapter.compare(["A", "B"])
    assert result.status is SourceStatus.OK
    assert result.data is not None and result.data["count"] == 2
    assert result.api_calls == 12
    assert result.billing_units == 12
    assert result.charged is True


async def test_adapter_records_metadata() -> None:
    class FakeClient:
        url = "https://mcp.example/mcp"

        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> McpToolResult:
            return McpToolResult(
                tool=tool,
                payload={"candidates": [{"ent_name": "示例科技有限公司"}]},
                fetched_at=datetime.now(UTC),
                raw_ref="s3://bucket/raw.json",
                api_calls=1,
                billing_units=1,
                order_no="order-2",
            )

    adapter = EnterpriseIntelAdapter(FakeClient())  # type: ignore[arg-type]
    result = await adapter.resolve("示例科技")
    assert result.status is SourceStatus.OK
    assert result.api_calls == 1
    assert result.billing_units == 1
    assert result.raw_ref == "s3://bucket/raw.json"
    assert result.order_no == "order-2"
