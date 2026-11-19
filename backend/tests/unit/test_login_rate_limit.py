import pytest

from app.modules.identity import rate_limit


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expires[key] = seconds

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_login_failures_are_limited_and_reset(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_client", lambda: fake)

    assert await rate_limit.login_allowed("203.0.113.9", "user@example.com")
    for _ in range(5):
        await rate_limit.record_login_failure("203.0.113.9", "user@example.com")
    assert not await rate_limit.login_allowed("203.0.113.9", "user@example.com")
    assert fake.expires

    await rate_limit.reset_login_failures("user@example.com")
    assert await rate_limit.login_allowed("203.0.113.9", "user@example.com")
