import httpx


class LLMConfigError(Exception):
    pass


class LLMTransportError(Exception):
    pass


class LLMResponseError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def complete(self, *, system: str, user: str) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise LLMConfigError("LLM 网关未配置")
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, trust_env=False
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMTransportError(
                f"LLM request failed: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("LLM returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM returned empty content")
        return content


def build_llm_client_from_settings() -> LLMClient:
    from app.config import get_settings

    settings = get_settings()
    return LLMClient(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
    )
