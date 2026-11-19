import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.modules.gateway.company_adapter import KeepRiskCompanyIntelAdapter
from app.modules.gateway.contract import SourceStatus
from app.modules.gateway.keeprisk_client import KeepRiskClient
from app.modules.gateway.snapshots import LocalSnapshotStore

PROFILE_PAYLOAD = {
    "code": "100",
    "message": "ok",
    "orderNo": "profile-order",
    "charge": True,
    "data": {
        "total": 1,
        "list": [
            {
                "subjectName": "测试科技有限公司",
                "subjectStatus": "存续",
                "subjectType": "有限责任公司",
                "province": "辽宁省",
                "city": "盘锦市",
                "county": "兴隆台区",
                "roles": [1, 2],
                "relatedCount": "12",
                "bidderCount": 3,
                "winnerCount": 2,
                "agentCount": 1,
                "tenderCount": 6,
                "publishDate": "2026-09-01 10:00:00",
                "title": "某项目中标公告",
                "role": "中标方",
                "infoType": "3",
            }
        ],
    },
}

BIDS_PAYLOAD = {
    "code": "100",
    "message": "ok",
    "orderNo": "bids-order",
    "charge": True,
    "data": {
        "total": 1,
        "list": [
            {
                "uid": "notice-1",
                "title": "某项目中标公告",
                "projectNo": "P-2026-001",
                "infoType": 3,
                "publishDate": "2026-09-01 10:00:00",
                "bidEntInfos": "某采购人",
                "winEidInfos": "测试科技有限公司",
                "winBidAmount": "1,298,520.00",
                "maxWinBidAmount": 1298520.0,
                "sourceUrl": "https://example.com/notice/1",
            }
        ],
    },
}


@pytest.fixture
def adapter(tmp_path) -> KeepRiskCompanyIntelAdapter:
    return KeepRiskCompanyIntelAdapter(
        KeepRiskClient(
            base_url="https://keeprisk.test/api/data",
            app_code="secret-app-code",
            timeout_seconds=1,
            snapshot_store=LocalSnapshotStore(str(tmp_path)),
        )
    )


@respx.mock
async def test_profile_uses_ent_name_object_and_normalizes(adapter) -> None:
    route = respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, json=PROFILE_PAYLOAD)
    )

    result = await adapter.fetch_profile("测试科技有限公司")

    assert result.status == SourceStatus.OK
    request = route.calls[0].request
    assert request.headers["pcode"] == "E_007_1341"
    body = json.loads(request.content)
    assert body["entName"] == {"eq": "测试科技有限公司", "type": "1"}
    assert body["pageSize"] == "20"
    assert result.order_no == "profile-order"
    assert result.charged is True
    assert result.data is not None
    entry = result.data.entries[0]
    assert entry.subject_name == "测试科技有限公司"
    assert entry.region == "辽宁省 盘锦市 兴隆台区"
    assert entry.roles == ["tenderer", "winner"]
    assert (entry.tender_count, entry.winner_count, entry.bidder_count) == (6, 2, 3)
    assert entry.latest_title == "某项目中标公告"


@respx.mock
async def test_profile_empty_status(adapter) -> None:
    respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(
            200, json={"code": "110", "message": "no records", "data": None}
        )
    )

    result = await adapter.fetch_profile("测试科技有限公司")

    assert result.status == SourceStatus.EMPTY
    assert result.data is not None
    assert result.data.entries == []


@respx.mock
async def test_profile_gateway_error_maps_to_status(adapter) -> None:
    respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, json={"code": "101", "message": "bad params"})
    )

    result = await adapter.fetch_profile("测试科技有限公司")

    assert result.status == SourceStatus.CONTRACT_ERROR
    assert result.error is not None
    assert result.error.code == "gateway_101"


@respx.mock
async def test_bids_use_search_key_and_award_filter(adapter) -> None:
    route = respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, json=BIDS_PAYLOAD)
    )

    result = await adapter.fetch_bids("测试科技有限公司", role="winner")

    assert result.status == SourceStatus.OK
    request = route.calls[0].request
    assert request.headers["pcode"] == "E_007_1271"
    body = json.loads(request.content)
    assert body["searchKey"] == {"type": "4", "text": "测试科技有限公司"}
    assert body["InfoTypeList"] == ["3"]
    assert body["contentFlag"] == "0"
    assert result.data is not None
    item = result.data[0]
    assert item.source_id == "notice-1"
    assert item.winner == "测试科技有限公司"
    assert item.win_bid_amount == Decimal("1298520.0")


@respx.mock
async def test_tenderer_history_has_no_award_only_filter(adapter) -> None:
    route = respx.post("https://keeprisk.test/api/data").mock(
        return_value=httpx.Response(200, json={"code": "110", "message": "none", "data": None})
    )

    result = await adapter.fetch_bids("某采购人", role="tenderer")

    assert result.status == SourceStatus.EMPTY
    body = json.loads(route.calls[0].request.content)
    assert body["searchKey"] == {"type": "3", "text": "某采购人"}
    assert "InfoTypeList" not in body


async def test_unsupported_role_is_rejected(adapter) -> None:
    with pytest.raises(ValueError):
        await adapter.fetch_bids("测试科技有限公司", role="unknown")
