import httpx
import pytest

from app.modules.gateway.snapshots import LocalSnapshotStore
from app.modules.gateway.web_search import (
    WebSearchClient,
    WebSearchConfigError,
)


def _client(
    *,
    provider: str,
    handler,
    tmp_path,
    deepseek_key: str | None = "test-deepseek",
    minimax_key: str | None = "test-minimax",
) -> WebSearchClient:
    return WebSearchClient(
        provider=provider,
        timeout_seconds=5,
        max_results=5,
        deepseek_api_key=deepseek_key,
        deepseek_base_url="https://search.test",
        minimax_search_key=minimax_key,
        minimax_base_url="https://minimax.test/anthropic",
        minimax_model="MiniMax-Test",
        snapshot_store=LocalSnapshotStore(str(tmp_path)),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_deepseek_search_normalizes_results(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/tools/search"
        assert request.headers["Authorization"] == "Bearer test-deepseek"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "政务云运维",
                        "url": "https://example.test/a",
                        "snippet": "双活架构",
                        "published_at": "2026-10-22",
                    }
                ]
            },
        )

    page = await _client(provider="deepseek", handler=handler, tmp_path=tmp_path).search(
        "政务云运维"
    )
    assert page.status == "ok"
    assert page.items[0].title == "政务云运维"
    assert page.items[0].url == "https://example.test/a"
    assert page.items[0].snippet == "双活架构"
    assert page.raw_ref.endswith(".json")
    assert not page.raw_ref.startswith(str(tmp_path))
    assert (tmp_path / page.raw_ref).is_file()


@pytest.mark.asyncio
async def test_minimax_search_extracts_tool_results(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/anthropic/v1/messages"
        assert request.headers["x-api-key"] == "test-minimax"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "答案"},
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {
                                "title": "DeepSeek 搜索",
                                "url": "https://example.test/b",
                                "content": "服务端工具",
                                "page_age": "1d",
                            }
                        ],
                    },
                ]
            },
        )

    page = await _client(provider="minimax", handler=handler, tmp_path=tmp_path).search(
        "DeepSeek 搜索"
    )
    assert page.status == "ok"
    assert page.items[0].title == "DeepSeek 搜索"
    assert page.items[0].snippet == "服务端工具"
    assert page.items[0].published_at == "1d"


@pytest.mark.asyncio
async def test_search_filters_non_http_urls(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "恶意", "url": "javascript:alert(1)"},
                    {"title": "相对", "url": "/relative"},
                    {"title": "正常", "url": "https://example.test/ok"},
                ]
            },
        )

    page = await _client(provider="deepseek", handler=handler, tmp_path=tmp_path).search(
        "测试"
    )
    assert [item.url for item in page.items] == ["https://example.test/ok"]


@pytest.mark.asyncio
async def test_missing_provider_key_fails_before_request(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    client = _client(
        provider="deepseek",
        handler=handler,
        tmp_path=tmp_path,
        deepseek_key=None,
    )
    with pytest.raises(WebSearchConfigError):
        await client.search("测试")
