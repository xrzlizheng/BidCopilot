from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.modules.company_intel import intel_router
from app.modules.gateway.contract import DataError, DataResult, SourceStatus


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeAudit:
    entries: list[dict[str, Any]] = []

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def _user() -> SimpleNamespace:
    return SimpleNamespace(tenant_id=uuid4(), id=uuid4())


def _result(*, status: SourceStatus, charged: bool, billing_units: int = 2) -> DataResult[dict]:
    return DataResult(
        status=status,
        data={"ok": True} if status is SourceStatus.OK else None,
        source="keeprisk-mcp",
        fetched_at=datetime.now(UTC),
        error=DataError(code="upstream", message="failed") if status is not SourceStatus.OK else None,
        charged=charged,
        api_calls=4,
        billing_units=billing_units,
    )


async def _run(monkeypatch, result: DataResult[dict], *, units: int = 4) -> tuple[dict, dict, list[dict]]:
    reservations = []
    released = []
    recorded: dict[str, Any] = {}

    async def fake_reserve(tenant_id, *, amount=1):
        reservation = SimpleNamespace(tenant_id=tenant_id, periods=(("day", None, 1000),), amount=amount)
        reservations.append(reservation)
        return reservation

    async def fake_release(reservation):
        released.append(reservation.amount)

    async def fake_record(session, **kwargs):
        recorded.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(intel_router, "reserve_paid_call", fake_reserve)
    monkeypatch.setattr(intel_router, "release_paid_call", fake_release)
    monkeypatch.setattr(intel_router, "record_gateway_call", fake_record)
    monkeypatch.setattr(intel_router, "AuditService", FakeAudit)
    FakeAudit.entries = []

    async def call() -> DataResult[dict]:
        return result

    payload = await intel_router._execute(
        user=_user(),
        session=FakeSession(),
        tool="enterprise_basic_profile",
        units=units,
        operation="profile",
        resource_id="示例科技有限公司",
        arguments={"ent_name": "示例科技有限公司"},
        call=call,
    )
    return payload, recorded, released


async def test_execute_releases_unused_quota(monkeypatch) -> None:
    payload, recorded, released = await _run(
        monkeypatch, _result(status=SourceStatus.OK, charged=True, billing_units=2), units=4
    )
    assert payload["status"] == "ok"
    assert released == [2]
    assert recorded["pcode"] == "mcp:enterprise_basic_profile"
    assert recorded["rows"] == 4
    assert recorded["charged"] is True
    assert FakeAudit.entries[0]["action"] == "company_intel.profile"


async def test_execute_releases_full_quota_when_not_charged(monkeypatch) -> None:
    _, recorded, released = await _run(
        monkeypatch, _result(status=SourceStatus.EMPTY, charged=False, billing_units=0), units=4
    )
    assert released == [4]
    assert recorded["status"] == "empty"


async def test_execute_keeps_quota_on_failure_result(monkeypatch) -> None:
    _, recorded, released = await _run(
        monkeypatch, _result(status=SourceStatus.CONTRACT_ERROR, charged=False, billing_units=0), units=1
    )
    assert released == [1]
    assert recorded["status"] == "contract_error"
