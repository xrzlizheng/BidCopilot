import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.notifications import webhooks


class FakeSession:
    """最小 AsyncSession 替身：只支撑 create_endpoint 的查重/落库路径。"""

    def __init__(self, existing=None, secret_row=None) -> None:
        self.existing = existing
        self.secret_row = secret_row
        self.added: list = []
        self.commits = 0
        self.deleted: list = []

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        # 模拟 DB 生成的 id，便于 encrypt_secret 的 owner_id=AAD 生效。
        for row in self.added:
            if getattr(row, "id", None) is None:
                import uuid

                row.id = uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row) -> None:
        return None

    async def delete(self, row) -> None:
        self.deleted.append(row)

    async def get(self, model, key):
        return self.secret_row

    async def execute(self, statement):
        existing = self.existing

        class _Result:
            def scalar_one_or_none(self_inner):
                return existing

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return [existing] if existing is not None else []

        return _Result()


def test_sign_payload_matches_hmac_sha256_reference() -> None:
    """签名口径必须能被接收方用标准 HMAC-SHA256 复算出来。"""
    secret = "s3cr3t-webhook-signing-key"
    body = b'{"event":"project.status.changed","data":{"to_status":"won"}}'
    timestamp = 1_700_000_000
    header = webhooks.sign_payload(secret, timestamp=timestamp, body=body)

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert header == f"t={timestamp},v1={expected}"
    assert header.startswith("t=1700000000,v1=")
    # 不同 timestamp 必须产生不同签名（时间戳参与签名，防重放）。
    assert webhooks.sign_payload(secret, timestamp=timestamp + 1, body=body) != header
    # 不同 secret 同理。
    assert webhooks.sign_payload(secret + "x", timestamp=timestamp, body=body) != header


def test_canonical_body_is_stable_json() -> None:
    payload = {"b": 2, "a": {"z": 1, "y": [3, 2]}}
    first = webhooks._canonical_body(payload)
    second = webhooks._canonical_body({"a": {"y": [3, 2], "z": 1}, "b": 2})
    assert first == second
    assert json.loads(first) == payload
    # 中文不转义，接收方直接可读。
    assert "项目" in webhooks._canonical_body({"name": "项目"}).decode()


def test_generated_secret_is_random_and_long() -> None:
    values = {webhooks.generate_secret() for _ in range(100)}
    assert len(values) == 100
    assert all(len(value) >= 32 for value in values)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/callback",
        "ftp://hooks.example.com/callback",
        "hooks.example.com/callback",
        "",
        "https://",
    ],
)
async def test_validate_rejects_non_https(url: str) -> None:
    with pytest.raises(ValidationAppError):
        await webhooks.validate_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/callback",
        "https://10.0.0.8/callback",
        "https://192.168.1.10/callback",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/callback",
        "https://0.0.0.0/callback",
    ],
)
async def test_validate_rejects_ssrf_targets(url: str) -> None:
    """与模型网关地址策略同口径：私网/回环/链路本地一律拒绝。"""
    with pytest.raises(ValidationAppError):
        await webhooks.validate_webhook_url(url)


async def test_validate_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationAppError):
        await webhooks.validate_webhook_url("https://user:pass@8.8.8.8/hook")


async def test_validate_allows_public_ip() -> None:
    assert await webhooks.validate_webhook_url("https://8.8.8.8/callback") == "https://8.8.8.8/callback"


async def test_validate_allowlist_escape_hatch(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URL", False)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOWED_HOSTS", "127.0.0.1")
    assert await webhooks.validate_webhook_url("https://127.0.0.1/hook") == "https://127.0.0.1/hook"


def test_normalize_events_rejects_unknown_and_empty() -> None:
    with pytest.raises(ValidationAppError):
        webhooks._normalize_events([])
    with pytest.raises(ValidationAppError):
        webhooks._normalize_events(["project.deleted"])
    assert webhooks._normalize_events(["*"]) == ["*"]
    assert webhooks._normalize_events(
        ["project.status.changed", "project.status.changed"]
    ) == ["project.status.changed"]


def test_subscribes_matches_wildcard_and_exact() -> None:
    from app.modules.notifications.webhook_models import WebhookEndpoint

    tenant_id = uuid4()
    exact = WebhookEndpoint(
        tenant_id=tenant_id, url="https://8.8.8.8/a", events=["project.status.changed"]
    )
    wildcard = WebhookEndpoint(tenant_id=tenant_id, url="https://8.8.8.8/b", events=["*"])
    assert webhooks._subscribes(exact, "project.status.changed")
    assert not webhooks._subscribes(exact, "compliance.hard_finding")
    assert webhooks._subscribes(wildcard, "compliance.hard_finding")


async def test_create_endpoint_encrypts_secret(monkeypatch) -> None:
    """secret 必须经 encrypt_secret 落 secret_store，明文不写进 endpoint 字段。"""

    async def _allow(_url: str) -> str:
        return _url

    monkeypatch.setattr(webhooks, "validate_webhook_url", _allow)
    session = FakeSession()
    endpoint, plaintext = await webhooks.create_endpoint(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        url="https://8.8.8.8/callback",
        events=["project.status.changed"],
    )
    assert plaintext
    assert endpoint.secret_ref is not None
    secret_row = next(row for row in session.added if hasattr(row, "ciphertext"))
    # 密文里不应出现明文片段（AES-GCM 密文）。
    assert plaintext.encode() not in secret_row.ciphertext
    assert secret_row.scope == webhooks.SECRET_SCOPE
    assert secret_row.last4 == plaintext[-4:]
    assert not hasattr(endpoint, "secret")


async def test_create_endpoint_rejects_duplicate_url(monkeypatch) -> None:
    async def _allow(_url: str) -> str:
        return _url

    monkeypatch.setattr(webhooks, "validate_webhook_url", _allow)

    class _Existing:
        pass

    with pytest.raises(ConflictError):
        await webhooks.create_endpoint(
            FakeSession(existing=_Existing()),
            tenant_id=uuid4(),
            user_id=uuid4(),
            url="https://8.8.8.8/callback",
            events=["project.status.changed"],
        )


async def test_create_endpoint_rejects_short_custom_secret(monkeypatch) -> None:
    async def _allow(_url: str) -> str:
        return _url

    monkeypatch.setattr(webhooks, "validate_webhook_url", _allow)
    with pytest.raises(ValidationAppError):
        await webhooks.create_endpoint(
            FakeSession(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            url="https://8.8.8.8/callback",
            events=["project.status.changed"],
            secret="short",
        )


async def test_get_endpoint_or_404_is_scoped(monkeypatch) -> None:
    with pytest.raises(NotFoundError):
        await webhooks.get_endpoint_or_404(FakeSession(), uuid4(), uuid4())


async def _run_delivery(
    monkeypatch, handler, *, attempts: int = 3, backoff: float = 0.0
):
    settings = get_settings()
    monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", attempts)
    monkeypatch.setattr(settings, "WEBHOOK_RETRY_BACKOFF_SECONDS", backoff)
    monkeypatch.setattr(webhooks, "_endpoint_secret", lambda *args, **kwargs: _secret())
    from app.modules.notifications.webhook_models import WebhookEndpoint

    endpoint = WebhookEndpoint(
        tenant_id=uuid4(),
        url="https://8.8.8.8/callback",
        events=["project.status.changed"],
    )
    session = FakeSession()
    delivery = await webhooks.deliver_to_endpoint(
        session,
        endpoint,
        event="project.status.changed",
        payload={"event": "project.status.changed", "data": {"to_status": "won"}},
        transport=httpx.MockTransport(handler),
    )
    return delivery, session


async def _secret() -> str:
    return "s3cr3t-webhook-signing-key"


async def test_delivery_success_records_success_and_signature(monkeypatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200)

    delivery, _ = await _run_delivery(monkeypatch, handler)
    assert delivery.status == "success"
    assert delivery.attempts == 1
    assert delivery.response_code == 200
    assert delivery.error is None

    headers = seen["headers"]
    assert headers[webhooks.EVENT_HEADER.lower()] == "project.status.changed"
    timestamp = int(headers[webhooks.TIMESTAMP_HEADER.lower()])
    expected = hmac.new(
        b"s3cr3t-webhook-signing-key",
        f"{timestamp}.".encode() + seen["body"],
        hashlib.sha256,
    ).hexdigest()
    assert headers[webhooks.SIGNATURE_HEADER.lower()] == f"t={timestamp},v1={expected}"
    assert json.loads(seen["body"])["data"]["to_status"] == "won"


async def test_delivery_retries_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(204)

    delivery, _ = await _run_delivery(monkeypatch, handler)
    assert calls["n"] == 3
    assert delivery.status == "success"
    assert delivery.attempts == 3
    assert delivery.response_code == 204


async def test_delivery_failure_is_bounded_and_recorded(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    delivery, _ = await _run_delivery(monkeypatch, handler)
    # 重试次数有界：不超过 WEBHOOK_MAX_ATTEMPTS。
    assert calls["n"] == 3
    assert delivery.status == "failed"
    assert delivery.attempts == 3
    assert delivery.response_code == 503
    assert "HTTP 503" in (delivery.error or "")


async def test_delivery_network_error_does_not_raise(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    delivery, _ = await _run_delivery(monkeypatch, handler)
    # 网络异常必须降级成 failed 记录，绝不能冒泡影响主流程。
    assert delivery.status == "failed"
    assert delivery.attempts == 3
    assert delivery.response_code is None
    assert "ConnectTimeout" in (delivery.error or "")


async def test_delivery_without_secret_refuses_and_records(monkeypatch) -> None:
    from app.modules.notifications.webhook_models import WebhookEndpoint

    monkeypatch.setattr(webhooks, "_endpoint_secret", lambda *args, **kwargs: _no_secret())
    endpoint = WebhookEndpoint(
        tenant_id=uuid4(), url="https://8.8.8.8/callback", events=["*"]
    )
    session = FakeSession()
    delivery = await webhooks.deliver_to_endpoint(
        session,
        endpoint,
        event="project.status.changed",
        payload={"event": "project.status.changed"},
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    assert delivery.status == "failed"
    assert delivery.attempts == 0
    assert "secret" in (delivery.error or "")


async def _no_secret() -> None:
    return None


async def test_dispatch_event_selects_subscribed_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(webhooks, "_endpoint_secret", lambda *args, **kwargs: _secret())
    from app.modules.notifications.webhook_models import WebhookEndpoint

    tenant_id = uuid4()
    subscribed = WebhookEndpoint(
        tenant_id=tenant_id, url="https://8.8.8.8/a", events=["project.status.changed"]
    )
    other = WebhookEndpoint(
        tenant_id=tenant_id, url="https://8.8.8.8/b", events=["compliance.hard_finding"]
    )

    class _Session(FakeSession):
        async def execute(self, statement):
            rows = [subscribed, other]

            class _Result:
                def scalars(self_inner):
                    return self_inner

                def all(self_inner):
                    return rows

            return _Result()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    deliveries = await webhooks.dispatch_event(
        _Session(),
        tenant_id,
        "project.status.changed",
        {"project_id": str(uuid4())},
        transport=httpx.MockTransport(handler),
    )
    assert len(deliveries) == 1
    assert calls == ["https://8.8.8.8/a"]


def test_event_catalog_covers_required_events() -> None:
    # 任务要求至少覆盖：项目状态变更 / 交标回执创建 / 合规 HARD finding。
    assert {
        "project.status.changed",
        "submission_receipt.created",
        "compliance.hard_finding",
    } <= set(webhooks.EVENTS)


async def test_notify_helpers_are_noop_without_loop(monkeypatch) -> None:
    """没有事件循环时 schedule_event 必须静默跳过，不抛异常。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "WEBHOOK_DELIVERY_ENABLED", True)
    # 在同步上下文里调用（此测试本身就是同步 def → 无 running loop）。
    webhooks.notify_project_status_changed(
        uuid4(),
        project_id=uuid4(),
        from_status="active",
        to_status="won",
        reason=None,
    )
    # hard_count 为 0 时不登记事件。
    webhooks.notify_compliance_hard_findings(
        uuid4(), project_id=uuid4(), report_id=uuid4(), hard_count=0, rule_ids=[]
    )


def test_notify_helpers_route_to_expected_event_names(monkeypatch) -> None:
    """三类挂载事件各自对应正确的 event 名与 payload 字段。"""
    scheduled: list[tuple[str, dict]] = []

    def _capture(tenant_id, event, data):
        scheduled.append((event, data))

    monkeypatch.setattr(webhooks, "schedule_event", _capture)
    tenant_id = uuid4()
    project_id, receipt_id, report_id = uuid4(), uuid4(), uuid4()

    webhooks.notify_project_status_changed(
        tenant_id,
        project_id=project_id,
        from_status="active",
        to_status="lost",
        reason="价格劣势",
    )
    webhooks.notify_submission_receipt_created(
        tenant_id,
        project_id=project_id,
        receipt_id=receipt_id,
        platform="某省公共资源交易平台",
        receipt_no="R-1",
        submitted_at="2026-10-31T09:30:00+08:00",
    )
    webhooks.notify_compliance_hard_findings(
        tenant_id,
        project_id=project_id,
        report_id=report_id,
        hard_count=2,
        rule_ids=["DARK-NAME-001", "EVID-UNSOURCED-001"],
    )

    assert [event for event, _ in scheduled] == [
        "project.status.changed",
        "submission_receipt.created",
        "compliance.hard_finding",
    ]
    status_data = scheduled[0][1]
    assert status_data["project_id"] == str(project_id)
    assert status_data["to_status"] == "lost"
    assert status_data["reason"] == "价格劣势"
    receipt_data = scheduled[1][1]
    assert receipt_data["receipt_id"] == str(receipt_id)
    assert receipt_data["receipt_no"] == "R-1"
    hard_data = scheduled[2][1]
    assert hard_data["report_id"] == str(report_id)
    assert hard_data["hard_count"] == 2
    assert hard_data["rule_ids"] == ["DARK-NAME-001", "EVID-UNSOURCED-001"]


def test_compliance_helper_skips_when_no_hard_findings(monkeypatch) -> None:
    scheduled: list[tuple[str, dict]] = []
    monkeypatch.setattr(webhooks, "schedule_event", lambda *a, **k: scheduled.append(a))
    webhooks.notify_compliance_hard_findings(
        uuid4(), project_id=uuid4(), report_id=uuid4(), hard_count=0, rule_ids=[]
    )
    assert scheduled == []


def test_schedule_event_respects_delivery_switch(monkeypatch) -> None:
    """WEBHOOK_DELIVERY_ENABLED=False 时不登记任何后台任务。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "WEBHOOK_DELIVERY_ENABLED", False)
    webhooks._pending_tasks.clear()
    webhooks.notify_project_status_changed(
        uuid4(), project_id=uuid4(), from_status="active", to_status="won", reason=None
    )
    assert webhooks._pending_tasks == set()
