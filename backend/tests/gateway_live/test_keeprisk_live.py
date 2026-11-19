import os
import shutil
import tempfile

import pytest

from app.config import get_settings
from app.modules.gateway.contract import SourceStatus
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter, OpportunityQuery
from app.modules.gateway.keeprisk_client import KeepRiskClient
from app.modules.gateway.snapshots import LocalSnapshotStore

pytestmark = [
    pytest.mark.gateway_live,
    pytest.mark.skipif(os.environ.get("GATEWAY_LIVE") != "1", reason="GATEWAY_LIVE is not enabled"),
    pytest.mark.skipif(
        os.environ.get("ENABLE_MOCK_MODE") != "false",
        reason="ENABLE_MOCK_MODE must be explicitly false",
    ),
]


async def test_live_list_and_detail() -> None:
    settings = get_settings()
    snapshot_dir = tempfile.mkdtemp(prefix="bidcopilot-live-")
    try:
        adapter = KeepRiskTenderAdapter(
            KeepRiskClient(
                base_url=settings.KEEPRISK_API_BASE_URL,
                app_code=settings.KEEPRISK_APP_CODE,
                timeout_seconds=settings.KEEPRISK_TIMEOUT_SECONDS,
                snapshot_store=LocalSnapshotStore(snapshot_dir),
            )
        )
        listing = await adapter.fetch_page(
            OpportunityQuery(keywords=["系统集成"], info_types=["1"], page_size=1)
        )
        assert listing.status == SourceStatus.OK, listing.error
        assert listing.data
        item = listing.data[0]
        assert item.title
        assert item.source_id

        detail = await adapter.fetch_detail([item.source_id])
        assert detail.status == SourceStatus.OK, detail.error
        assert detail.data
        full = [candidate for candidate in detail.data if candidate.detail_status == "full"]
        assert full
        assert full[0].content_html
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
