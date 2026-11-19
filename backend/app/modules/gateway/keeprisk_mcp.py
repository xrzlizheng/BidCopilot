import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.modules.gateway.snapshots import SnapshotStore

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
MCP_ACCEPT = "application/json, text/event-stream"


class McpConfigError(Exception):
    pass


class McpTransportError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class McpResponseError(Exception):
    def __init__(self, message: str, *, raw_ref: str | None = None) -> None:
        self.raw_ref = raw_ref
        super().__init__(message)


class McpToolError(McpResponseError):
    pass


@dataclass(frozen=True, slots=True)
class McpToolResult:
    tool: str
    payload: dict[str, Any]
    fetched_at: datetime
    raw_ref: str | None = None
    api_calls: int = 0
    billing_units: int = 0
    order_no: str | None = None
    elapsed_ms: int | None = None

    @property
    def charged(self) -> bool:
        if self.billing_units > 0:
            return True
        return bool(self.payload.get("charge"))


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class KeepRiskMcpClient:
    def __init__(
        self,
        *,
        url: str | None,
        token: str | None,
        timeout_seconds: float,
        snapshot_store: SnapshotStore,
        max_response_bytes: int = 20 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.snapshot_store = snapshot_store
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self._session_id: str | None = None
        self._init_lock = asyncio.Lock()

    def _require_config(self) -> None:
        if not self.url:
            raise McpConfigError("KEEPRISK_MCP_URL is not configured")
        if not self.token:
            raise McpConfigError("KEEPRISK_MCP_TOKEN is not configured")

    def _headers(self, session_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": MCP_ACCEPT,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> httpx.Response:
        try:
            if not self.url:
                raise McpConfigError("KEEPRISK_MCP_URL is not configured")
            response = await client.post(self.url, json=payload, headers=self._headers(session_id))
        except httpx.HTTPError as exc:
            raise McpTransportError(f"MCP request failed: {exc}") from exc
        if response.status_code >= 400:
            raise McpTransportError(
                f"MCP returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response

    async def _ensure_session(self, client: httpx.AsyncClient) -> str:
        if self._session_id:
            return self._session_id
        async with self._init_lock:
            if self._session_id:
                return self._session_id
            response = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "bidcopilot", "version": "0.1.0"},
                    },
                },
            )
            session_id = response.headers.get("mcp-session-id")
            if not session_id:
                raise McpResponseError("MCP did not return mcp-session-id")
            try:
                await self._post(
                    client,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    session_id,
                )
            except McpTransportError:
                logger.warning("MCP initialized notification failed", exc_info=True)
            self._session_id = session_id
            return session_id

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            events: list[str] = []
            current: list[str] = []
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    current.append(line[5:].lstrip())
                elif not line.strip() and current:
                    events.append("\n".join(current))
                    current = []
            if current:
                events.append("\n".join(current))
            for event in events:
                try:
                    parsed = json.loads(event)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                    return parsed
            raise McpResponseError("MCP returned no JSON-RPC response in event stream")
        try:
            parsed = response.json()
        except ValueError as exc:
            raise McpResponseError("MCP returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise McpResponseError("MCP returned a non-object payload")
        return parsed

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> McpToolResult:
        self._require_config()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
            transport=self.transport,
            trust_env=False,
        ) as client:
            session_id = await self._ensure_session(client)
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
            try:
                response = await self._post(client, request, session_id)
            except McpTransportError as exc:
                if exc.status_code not in {400, 404}:
                    raise
                # 会话失效只重建会话，不重发业务调用：首次 tools/call 可能已执行，
                # 盲目重发会造成双倍计费/重复副作用。
                self._session_id = None
                await self._ensure_session(client)
                raise

        raw = response.content
        if len(raw) > self.max_response_bytes:
            raise McpResponseError(f"MCP response exceeds {self.max_response_bytes} bytes")
        raw_ref = await self.snapshot_store.put_bytes(
            f"keeprisk-mcp/{tool}/{datetime.now(UTC).strftime('%Y%m%d')}/{uuid4().hex}.json",
            raw,
        )
        body = self._decode(response)
        if "error" in body:
            error = body.get("error") or {}
            raise McpToolError(
                f"MCP {tool} failed: {error.get('message', 'unknown error')}",
                raw_ref=raw_ref,
            )
        result = body.get("result") or {}
        if not isinstance(result, dict):
            raise McpResponseError("MCP result is not an object", raw_ref=raw_ref)
        text = ""
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text += str(part.get("text") or "")
        if result.get("isError"):
            raise McpToolError(f"MCP {tool} returned an error: {text[:500]}", raw_ref=raw_ref)
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise McpResponseError("MCP tool content is not JSON", raw_ref=raw_ref) from exc
        if not isinstance(payload, dict):
            payload = {"data": payload}
        order_no = payload.get("orderNo") or payload.get("order_no")
        return McpToolResult(
            tool=tool,
            payload=payload,
            fetched_at=datetime.now(UTC),
            raw_ref=raw_ref,
            api_calls=_as_int(payload.get("api_calls")),
            billing_units=_as_int(payload.get("billing_units")),
            order_no=str(order_no) if order_no else None,
            elapsed_ms=_as_int(payload.get("elapsed_ms")) or None,
        )
