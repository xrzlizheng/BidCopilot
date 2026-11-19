from pathlib import Path

import pytest

from app.modules.gateway.snapshots import LocalSnapshotStore


@pytest.mark.asyncio
async def test_local_snapshot_store_confines_keys(tmp_path: Path) -> None:
    store = LocalSnapshotStore(str(tmp_path / "snapshots"))
    ref = await store.put_bytes("tenant/a.json", b"payload")
    assert await store.get_bytes(ref) == b"payload"

    with pytest.raises(ValueError):
        await store.put_bytes("../escape.json", b"payload")
    with pytest.raises(ValueError):
        await store.get_bytes(str(tmp_path / "outside.json"))
    with pytest.raises(ValueError):
        await store.delete_bytes(str(tmp_path / "outside.json"))
