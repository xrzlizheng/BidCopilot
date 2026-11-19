import logging

import httpx
import pytest
import respx

from app.modules.gateway.contract import SourceStatus, status_for_gateway_code
from app.modules.gateway.keeprisk_client import (
    KeepRiskClient,
    KeepRiskConfigError,
    KeepRiskResponseError,
    KeepRiskTransportError,
)
from app.modules.gateway.snapshots import LocalSnapshotStore


@pytest.fixture
def client(tmp_path) -> KeepRiskClient:
    return KeepRiskClient(
        base_url="https://keeprisk.test/api/data",
        app_code="secret-app-code",
        timeout_seconds=1,
        snapshot_store=LocalSnapshotStore(str(tmp_path)),
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("100", SourceStatus.OK),
        ("110", SourceStatus.EMPTY),
        ("101", SourceStatus.CONTRACT_ERROR),
        ("104", SourceStatus.RATE_LIMITED),
        ("109", SourceStatus.RATE_LIMITED),
        ("103", SourceStatus.UNAUTHORIZED),
        ("105", SourceStatus.UNAUTHORIZED),
        ("108", SourceStatus.UNAUTHORIZED),
        ("107", SourceStatus.UNAVAILABLE),
        ("500", SourceStatus.CONTRACT_ERROR),
    ],
)
def test_gateway_code_mapping(code: str, expected: SourceStatus) -> None:
    assert status_for_gateway_code(code) == expected


@respx.mock
async def test_query_success_keeps_billing_metadata(client: KeepRiskClient, tmp_path) -> None:
    route = respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "100",
                "message": "ok",
                "orderNo": "order-1",
                "charge": True,
                "data": {"list": [{"uid": "u1"}]},
            },
        )
    )

    envelope = await client.query("E_007_1271", {"pageSize": "1"})

    assert route.called
    assert envelope.code == "100"
    assert envelope.order_no == "order-1"
    assert envelope.charge is True
    assert envelope.raw_ref is not None
    assert list(tmp_path.rglob("*.json"))


@respx.mock
async def test_invalid_json_raises_with_snapshot(client: KeepRiskClient) -> None:
    respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, content=b"not-json")
    )

    with pytest.raises(KeepRiskResponseError) as exc_info:
        await client.query("E_007_1271", {})

    assert exc_info.value.raw_ref


@respx.mock
async def test_transport_error(client: KeepRiskClient) -> None:
    respx.post("https://keeprisk.test/api/data").mock(side_effect=httpx.ReadTimeout("timeout"))

    with pytest.raises(KeepRiskTransportError):
        await client.query("E_007_1271", {})


async def test_missing_config_raises(tmp_path) -> None:
    client = KeepRiskClient(
        base_url=None,
        app_code=None,
        timeout_seconds=1,
        snapshot_store=LocalSnapshotStore(str(tmp_path)),
    )

    with pytest.raises(KeepRiskConfigError):
        await client.query("E_007_1271", {})


@respx.mock
async def test_app_code_is_not_logged(client: KeepRiskClient, caplog) -> None:
    caplog.set_level(logging.INFO)
    respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, json={"code": "100", "data": {}})
    )

    await client.query("E_007_1271", {})

    assert "secret-app-code" not in caplog.text
