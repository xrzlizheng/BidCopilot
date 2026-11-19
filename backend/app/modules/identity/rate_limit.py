import hashlib
import logging
import time

from fastapi import Request
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("bidcopilot.auth.rate_limit")

# Redis 不可用时的进程内降级计数（单容器/单进程为主；多副本时每副本各自计数，
# 仍能阻断暴力破解，且不会像 fail-open 那样完全放行）。
_fallback_counts: dict[str, tuple[int, float]] = {}


def _client() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )


def client_ip(request: Request) -> str:
    """取真实客户端 IP 用于限流。

    只信 `X-Real-IP`：nginx 用 `$remote_addr` 覆盖该头，客户端伪造不了。
    不能取 `X-Forwarded-For` 的第一段——nginx 用 `$proxy_add_x_forwarded_for`
    是**追加**语义，第一段恰恰是攻击者可控的假 IP，取它等于把限流关掉。
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _account_key(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
    return f"auth:login:account:{digest}"


def _ip_key(ip: str) -> str:
    return f"auth:login:ip:{ip}"


async def login_allowed(ip: str, email: str) -> bool:
    settings = get_settings()
    try:
        client = _client()
        try:
            account = int(await client.get(_account_key(email)) or 0)
            ip_failures = int(await client.get(_ip_key(ip)) or 0)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("login rate limit unavailable; using in-process fallback", exc_info=True)
        return _fallback_allowed(ip, email, settings)
    return (
        account < settings.LOGIN_MAX_ATTEMPTS
        and ip_failures < settings.LOGIN_IP_MAX_ATTEMPTS
    )


def _fallback_allowed(ip: str, email: str, settings) -> bool:
    now = time.monotonic()
    window = settings.LOGIN_WINDOW_SECONDS
    checks = (
        (_account_key(email), settings.LOGIN_MAX_ATTEMPTS),
        (_ip_key(ip), settings.LOGIN_IP_MAX_ATTEMPTS),
    )
    for key, limit in checks:
        count, started = _fallback_counts.get(key, (0, now))
        if now - started >= window:
            count = 0
        if count >= limit:
            return False
    return True


def _fallback_record(keys: tuple[str, ...], settings) -> None:
    now = time.monotonic()
    window = settings.LOGIN_WINDOW_SECONDS
    for key in keys:
        count, started = _fallback_counts.get(key, (0, now))
        if now - started >= window:
            count, started = 0, now
        _fallback_counts[key] = (count + 1, started)
    if len(_fallback_counts) > 10_000:
        stale = [key for key, (_, started) in _fallback_counts.items() if now - started >= window]
        for key in stale:
            _fallback_counts.pop(key, None)


def _fallback_clear(keys: tuple[str, ...]) -> None:
    for key in keys:
        _fallback_counts.pop(key, None)


async def record_login_failure(ip: str, email: str) -> None:
    settings = get_settings()
    keys = (_account_key(email), _ip_key(ip))
    try:
        client = _client()
        try:
            for key in keys:
                count = await client.incr(key)
                if count == 1:
                    await client.expire(key, settings.LOGIN_WINDOW_SECONDS)
        finally:
            await client.aclose()
    except Exception:
        # 仅 Redis 不可用时才落进程内降级计数。Redis 正常期间绝不累积 fallback：
        # 否则故障恢复后陈旧计数仍在，下次 Redis 抖动会直接误锁正常用户。
        logger.warning(
            "failed to record login failure in redis; using in-process fallback",
            exc_info=True,
        )
        _fallback_record(keys, settings)
        return
    # Redis 记账成功：清掉历史降级计数，计数口径始终以 Redis 为准。
    _fallback_clear(keys)


async def reset_login_failures(email: str, ip: str | None = None) -> None:
    """登录成功清零。传入 ip 时同时清理 IP 维度（fallback 与 Redis 双路）。"""
    keys = (_account_key(email), _ip_key(ip)) if ip is not None else (_account_key(email),)
    _fallback_clear(keys)
    try:
        client = _client()
        try:
            await client.delete(*keys)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("failed to reset login failures", exc_info=True)
