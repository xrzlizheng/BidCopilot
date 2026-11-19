import pytest

from app.config import get_settings
from app.modules.identity import rate_limit


@pytest.fixture(autouse=True)
def _clear_fallback():
    rate_limit._fallback_counts.clear()
    yield
    rate_limit._fallback_counts.clear()


def _break_redis(monkeypatch) -> None:
    def _broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "_client", _broken)


async def test_redis_outage_uses_in_process_fallback(monkeypatch) -> None:
    _break_redis(monkeypatch)
    ip, email = "10.0.0.9", "user@example.com"
    settings = get_settings()
    assert await rate_limit.login_allowed(ip, email) is True
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        await rate_limit.record_login_failure(ip, email)
    assert await rate_limit.login_allowed(ip, email) is False
    await rate_limit.reset_login_failures(email)
    assert await rate_limit.login_allowed(ip, email) is True
