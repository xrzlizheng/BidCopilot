import pytest

from app.modules.generation.llm_client import LLMClient, LLMResponseError
from app.modules.generation.llm_json import (
    RETRY_HINT,
    complete_json_object,
    parse_json_object,
)


class StubClient(LLMClient):
    """按顺序返回预置内容的假客户端（测试替身只允许存在于 tests/）。"""

    def __init__(self, contents: list[str]) -> None:
        super().__init__(base_url="http://stub/v1", api_key="k", model="stub-model")
        self.contents = contents
        self.calls: list[dict[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return self.contents[index]


def test_parse_json_object_handles_code_fence() -> None:
    content = '```json\n{"blocks": [{"type": "paragraph", "text": "hi"}]}\n```'
    assert parse_json_object(content)["blocks"][0]["text"] == "hi"


def test_parse_json_object_handles_leading_prose() -> None:
    assert parse_json_object('结果如下：{"ok": true} 以上')["ok"] is True


def test_error_message_never_echoes_model_output() -> None:
    """错误信息会经 AppError.message 回显给前端，绝不能带上模型原文。"""
    secret = "投标人内部报价 123 万元，联系人为张三"
    with pytest.raises(LLMResponseError) as excinfo:
        parse_json_object(f'{{"items": [{{"note": "{secret}"引号""}}]}}')
    message = str(excinfo.value)
    assert "不是合法 JSON" in message
    assert secret[:10] not in message
    assert "123 万元" not in message


def test_error_message_carries_position_but_not_content() -> None:
    with pytest.raises(LLMResponseError) as excinfo:
        parse_json_object('{"a": 1 "b": 2}')
    message = str(excinfo.value)
    assert "@" in message and ":" in message


async def test_complete_json_object_retries_once_and_appends_hint() -> None:
    client = StubClient(['{"blocks": [}', '{"blocks": [{"type": "paragraph", "text": "ok"}]}'])
    parsed = await complete_json_object(client, system="SYS", user="U", attempts=2)
    assert parsed["blocks"][0]["text"] == "ok"
    assert len(client.calls) == 2
    assert client.calls[0]["system"] == "SYS"
    assert RETRY_HINT in client.calls[1]["system"]


async def test_complete_json_object_stops_when_budget_exhausted() -> None:
    """总预算耗尽就停止重试，避免拖过反向代理读超时变成 504。"""
    client = StubClient(['{"blocks": [}', '{"blocks": [}', '{"blocks": [}'])
    with pytest.raises(LLMResponseError):
        await complete_json_object(
            client, system="SYS", user="U", attempts=3, total_budget_seconds=0.0
        )
    assert len(client.calls) == 1


async def test_complete_json_object_budget_zero_still_makes_first_attempt() -> None:
    client = StubClient(['{"blocks": [{"type": "paragraph", "text": "ok"}]}'])
    parsed = await complete_json_object(
        client, system="SYS", user="U", attempts=3, total_budget_seconds=0.0
    )
    assert parsed["blocks"][0]["text"] == "ok"
    assert len(client.calls) == 1


async def test_complete_json_object_raises_after_attempts_exhausted() -> None:
    client = StubClient(['{"blocks": [}', '{"blocks": [}', '{"blocks": [}'])
    with pytest.raises(LLMResponseError):
        await complete_json_object(client, system="SYS", user="U", attempts=3)
    assert len(client.calls) == 3
