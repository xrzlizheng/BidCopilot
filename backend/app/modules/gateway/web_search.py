import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.modules.gateway.snapshots import SnapshotStore


class WebSearchConfigError(Exception):
    pass


class WebSearchTransportError(Exception):
    pass


class WebSearchResponseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WebSearchItem:
    title: str
    url: str
    snippet: str
    published_at: str | None


@dataclass(frozen=True, slots=True)
class WebSearchPage:
    provider: str
    query: str
    status: str
    items: list[WebSearchItem]
    raw_ref: str
    fetched_at: datetime


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _result_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("results", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _result_list(value)
            if nested:
                return nested
    return []


def _safe_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _published_at(payload: dict[str, Any]) -> str | None:
    value = payload.get("published_at") or payload.get("page_age") or payload.get("date")
    return str(value).strip() if value is not None and str(value).strip() else None


class WebSearchClient:
    def __init__(
        self,
        *,
        provider: str,
        timeout_seconds: float,
        max_results: int,
        deepseek_api_key: str | None,
        deepseek_base_url: str,
        minimax_search_key: str | None,
        minimax_base_url: str,
        minimax_model: str,
        snapshot_store: SnapshotStore,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_base_url = deepseek_base_url
        self.minimax_search_key = minimax_search_key
        self.minimax_base_url = minimax_base_url
        self.minimax_model = minimax_model
        self.snapshot_store = snapshot_store
        self.transport = transport
        # 与 KeepRisk 客户端同口径的响应体上限，防止异常大响应打爆内存。
        self.max_response_bytes = 20 * 1024 * 1024

    def configured(self) -> bool:
        if self.provider == "deepseek":
            return bool(self.deepseek_api_key)
        return bool(self.minimax_search_key)

    async def search(self, query: str, *, count: int | None = None) -> WebSearchPage:
        limit = min(count or self.max_results, self.max_results)
        if self.provider == "deepseek":
            raw, items = await self._search_deepseek(query, limit)
        elif self.provider == "minimax":
            raw, items = await self._search_minimax(query, limit)
        else:
            raise WebSearchConfigError("WEB_SEARCH_PROVIDER must be deepseek or minimax")
        raw_ref = await self.snapshot_store.put_bytes(
            f"web-search/{uuid4()}.json",
            json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json",
        )
        return WebSearchPage(
            provider=self.provider,
            query=query,
            status="ok" if items else "empty",
            items=items,
            raw_ref=raw_ref,
            fetched_at=datetime.now(UTC),
        )

    async def _post(self, url: str, *, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebSearchTransportError(f"web search request failed: {exc}") from exc
        raw = response.content
        if len(raw) > self.max_response_bytes:
            raise WebSearchResponseError("web search response exceeds size limit")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise WebSearchResponseError("web search returned invalid JSON") from exc

    async def _search_deepseek(
        self, query: str, count: int
    ) -> tuple[Any, list[WebSearchItem]]:
        if not self.deepseek_api_key:
            raise WebSearchConfigError("DEEPSEEK_SEARCH_API_KEY is not configured")
        body = await self._post(
            f"{self.deepseek_base_url.rstrip('/')}/v1/tools/search",
            payload={"query": query, "count": count, "need_content": True},
            headers={
                "Authorization": f"Bearer {self.deepseek_api_key}",
                "Content-Type": "application/json",
            },
        )
        items: list[WebSearchItem] = []
        for row in _result_list(body):
            if not isinstance(row, dict):
                continue
            url = _safe_url(_first_text(row, "url", "link"))
            if not url:
                continue
            items.append(
                WebSearchItem(
                    title=_first_text(row, "title", "name") or url,
                    url=url,
                    snippet=_first_text(row, "snippet", "summary", "content"),
                    published_at=_published_at(row),
                )
            )
        return body, items[:count]

    async def _search_minimax(
        self, query: str, count: int
    ) -> tuple[Any, list[WebSearchItem]]:
        if not self.minimax_search_key:
            raise WebSearchConfigError("MINIMAX_SEARCH_API_KEY is not configured")
        body = await self._post(
            f"{self.minimax_base_url.rstrip('/')}/v1/messages",
            payload={
                "model": self.minimax_model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": f"搜索：{query}"}],
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            },
            headers={
                "x-api-key": self.minimax_search_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        content = body.get("content") if isinstance(body, dict) else None
        items: list[WebSearchItem] = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
                continue
            rows = block.get("content")
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                url = _safe_url(_first_text(row, "url", "link"))
                if not url:
                    continue
                items.append(
                    WebSearchItem(
                        title=_first_text(row, "title", "name") or url,
                        url=url,
                        snippet=_first_text(row, "content", "snippet", "summary"),
                        published_at=_published_at(row),
                    )
                )
        return body, items[:count]
