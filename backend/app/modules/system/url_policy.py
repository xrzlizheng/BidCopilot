import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.config import get_settings
from app.core.errors import ValidationAppError


def _allowed_hosts() -> set[str]:
    settings = get_settings()
    return {
        host.strip().lower()
        for host in settings.MODEL_ALLOWED_HOSTS.split(",")
        if host.strip()
    }


def _forbidden_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def forbidden_outbound_address(value: str) -> bool:
    """出网地址黑名单判定（模型网关与 webhook 共用同一口径）。

    公开入口供其他模块复用，避免复制一份容易漂移的私网判定。
    """
    return _forbidden_ip(value)


async def validate_model_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationAppError("模型网关地址必须是有效的 http(s) 地址")
    host = parsed.hostname.lower()
    settings = get_settings()
    if settings.MODEL_ALLOW_PRIVATE_BASE_URL or host in _allowed_hosts():
        return

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError as exc:
            raise ValidationAppError("模型网关地址无法解析") from exc
        addresses = {info[4][0] for info in infos}
        if not addresses or any(_forbidden_ip(str(address)) for address in addresses):
            raise ValidationAppError(
                "模型网关地址不能指向内网、环回或链路本地地址"
            ) from None
        return
    if _forbidden_ip(str(literal)):
        raise ValidationAppError("模型网关地址不能指向内网、环回或链路本地地址")
