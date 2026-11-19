import httpx
import pytest
import respx

from app.modules.generation.llm_client import (
    LLMClient,
    LLMConfigError,
    LLMResponseError,
    LLMTransportError,
)


@pytest.fixture
def client() -> LLMClient:
    return LLMClient(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        timeout_seconds=5,
    )


@respx.mock
async def test_complete_success(client: LLMClient) -> None:
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{\"blocks\": []}"}}]},
        )
    )

    content = await client.complete(system="system", user="user")

    assert route.called
    assert content == "{\"blocks\": []}"


async def test_missing_config() -> None:
    client = LLMClient(base_url=None, api_key=None, model=None)

    with pytest.raises(LLMConfigError):
        await client.complete(system="system", user="user")


@respx.mock
async def test_transport_error(client: LLMClient) -> None:
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(LLMTransportError):
        await client.complete(system="system", user="user")


@respx.mock
async def test_invalid_response(client: LLMClient) -> None:
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )

    with pytest.raises(LLMResponseError):
        await client.complete(system="system", user="user")
