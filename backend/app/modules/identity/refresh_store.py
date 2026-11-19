import logging
import time

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("bidcopilot.auth.refresh_store")

_REPLAY_PREFIX = "auth:refresh:used:"
# 并发刷新（多标签页/多请求同时 401）会在同一秒内重放同一 cookie：
# 宽限窗口内只拒绝该次刷新，不吊销整个用户会话；超窗重放视为令牌泄露。
REPLAY_GRACE_SECONDS = 60


def _client() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )


async def consume_refresh_jti(jti: str, ttl_seconds: int) -> str:
    """一次性消费 refresh jti。

    返回 fresh / replay / replay_old / unavailable。
    unavailable 表示重放存储本身不可用（fail-open，与令牌版本校验并存）。
    """
    try:
        client = _client()
        try:
            key = _REPLAY_PREFIX + jti
            stored = await client.set(
                key, str(int(time.time())), nx=True, ex=max(int(ttl_seconds), 1)
            )
            if stored:
                return "fresh"
            raw = await client.get(key)
            age = time.time() - int(raw) if raw else REPLAY_GRACE_SECONDS + 1
            return "replay" if age <= REPLAY_GRACE_SECONDS else "replay_old"
        finally:
            await client.aclose()
    except Exception:
        logger.warning("refresh replay store unavailable", exc_info=True)
        return "unavailable"
