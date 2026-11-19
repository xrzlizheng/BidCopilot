"""限流用的客户端 IP 取值：只信 nginx 覆盖写过的 X-Real-IP。

`X-Forwarded-For` 在 nginx 侧是 `$proxy_add_x_forwarded_for`（追加语义），
第一段正好是攻击者可控的内容；若拿它做限流键，伪造一个头就能绕过登录爆破防护。
"""

from starlette.requests import Request

from app.modules.identity.rate_limit import client_ip


def _request(headers: dict[str, str], client: tuple[str, int] | None = ("10.0.0.9", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


def test_client_ip_uses_x_real_ip() -> None:
    assert client_ip(_request({"x-real-ip": "203.0.113.7"})) == "203.0.113.7"


def test_client_ip_ignores_spoofed_forwarded_for() -> None:
    """伪造 X-Forwarded-For 不得改变限流键。"""
    spoofed = _request(
        {"x-forwarded-for": "1.2.3.4, 203.0.113.7", "x-real-ip": "203.0.113.7"}
    )
    assert client_ip(spoofed) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer_when_header_absent() -> None:
    assert client_ip(_request({})) == "10.0.0.9"


def test_client_ip_handles_missing_client() -> None:
    assert client_ip(_request({}, client=None)) == "unknown"
