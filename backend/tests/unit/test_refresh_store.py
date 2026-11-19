import time

import pytest

from app.modules.identity import refresh_store


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(refresh_store, "_client", lambda: client)
    return client


async def test_refresh_jti_is_single_use(fake_redis) -> None:
    assert await refresh_store.consume_refresh_jti("jti-1", 600) == "fresh"
    assert await refresh_store.consume_refresh_jti("jti-1", 600) == "replay"


async def test_old_replay_is_flagged_for_revocation(fake_redis) -> None:
    fake_redis.data[f"{refresh_store._REPLAY_PREFIX}jti-2"] = str(
        int(time.time()) - refresh_store.REPLAY_GRACE_SECONDS - 5
    )
    assert await refresh_store.consume_refresh_jti("jti-2", 600) == "replay_old"


async def test_store_unavailable_fails_open(monkeypatch) -> None:
    def _broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr(refresh_store, "_client", _broken)
    assert await refresh_store.consume_refresh_jti("jti-3", 600) == "unavailable"
