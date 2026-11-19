"""audit 哈希链修复纯函数测试：recompute_chain / _expected_hash（无 DB 依赖）。"""

import hashlib
import hmac

from app.modules.audit.service import _expected_hash, canonical_json, recompute_chain

KEY = "unit-test-secret-key-at-least-32-characters"
TENANT = "11111111-1111-1111-1111-111111111111"


def _payload(index: int) -> dict:
    return {
        "tenant_id": TENANT,
        "target_tenant_id": None,
        "actor_id": None,
        "action": "test.append",
        "resource_type": "test",
        "resource_id": str(index),
        "data": {"index": index},
        "created_at": f"2026-10-29T00:00:{index:02d}+00:00",
    }


def _chain(schemes: list[str], start_seq: int = 1) -> list[dict]:
    """按 scheme 序列构造自洽链；scheme 为 "v1" 或 "v2"。"""
    events: list[dict] = []
    previous = ""
    for offset, scheme in enumerate(schemes):
        payload = _payload(offset)
        marker = "v2:" if scheme == "v2" else ""
        digest = _expected_hash(previous, payload, marker, KEY)
        events.append(
            {
                "seq": start_seq + offset,
                "payload": payload,
                "prev_hash": previous,
                "hash": digest,
            }
        )
        previous = digest
    return events


def _verify_independent(events: list[dict], key: str) -> bool:
    """独立复算校验（不复用被测的 _expected_hash），镜像 verify_chain 语义。"""
    previous = ""
    for event in events:
        body = (previous + canonical_json(event["payload"])).encode("utf-8")
        if event["hash"].startswith("v2:"):
            expected = "v2:" + hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
        else:
            expected = hashlib.sha256(body).hexdigest()
        if event["prev_hash"] != previous or event["hash"] != expected:
            return False
        previous = event["hash"]
    return True


def _apply(events: list[dict], updates: list[dict]) -> list[dict]:
    by_seq = {event["seq"]: dict(event) for event in events}
    for update in updates:
        by_seq[update["seq"]]["prev_hash"] = update["prev_hash"]
        by_seq[update["seq"]]["hash"] = update["hash"]
    return [by_seq[event["seq"]] for event in events]


def test_expected_hash_scheme_follows_stored_prefix() -> None:
    payload = _payload(0)
    body = canonical_json(payload).encode("utf-8")
    v1 = _expected_hash("", payload, "a" * 64, KEY)
    assert v1 == hashlib.sha256(body).hexdigest()
    assert not v1.startswith("v2:")
    v2 = _expected_hash("", payload, "v2:" + "a" * 64, KEY)
    assert v2 == "v2:" + hmac.new(KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_healthy_mixed_chain_is_idempotent() -> None:
    events = _chain(["v1", "v1", "v2", "v2", "v2"])
    assert _verify_independent(events, KEY)
    updates, report = recompute_chain(events, KEY)
    assert updates == []
    assert report == {
        "event_count": 5,
        "first_bad_seq": None,
        "missing_seqs": [],
        "repaired_count": 0,
    }


def test_empty_chain_reports_no_break() -> None:
    updates, report = recompute_chain([], KEY)
    assert updates == []
    assert report["event_count"] == 0
    assert report["first_bad_seq"] is None
    assert report["missing_seqs"] == []
    assert report["repaired_count"] == 0


def test_pruned_middle_row_recomputes_from_break_and_preserves_scheme() -> None:
    events = _chain(["v1", "v1", "v1", "v2", "v2"])
    deleted = events[1]
    survivors = [events[0], *events[2:]]
    assert not _verify_independent(survivors, KEY)

    updates, report = recompute_chain(survivors, KEY)
    assert report["first_bad_seq"] == events[2]["seq"]
    assert report["missing_seqs"] == [deleted["seq"]]
    assert report["event_count"] == 4
    assert report["repaired_count"] == 3
    assert [update["seq"] for update in updates] == [event["seq"] for event in events[2:]]
    # scheme 跟随各行 stored hash：v1 行重算后仍无前缀，v2 行仍带 v2:
    assert not updates[0]["hash"].startswith("v2:")
    assert updates[1]["hash"].startswith("v2:")
    assert updates[2]["hash"].startswith("v2:")
    # 断点行 prev_hash 接到最后一个完好事件
    assert updates[0]["prev_hash"] == events[0]["hash"]

    repaired = _apply(survivors, updates)
    assert _verify_independent(repaired, KEY)
    # 修复后再跑幂等：无断点、无改写
    updates_again, report_again = recompute_chain(repaired, KEY)
    assert updates_again == []
    assert report_again["first_bad_seq"] is None
    assert report_again["repaired_count"] == 0


def test_break_at_first_event_prev_only_rewrites_minimal() -> None:
    events = _chain(["v2", "v1", "v2"])
    original_hash = events[0]["hash"]
    events[0]["prev_hash"] = "bogus"
    updates, report = recompute_chain(events, KEY)
    assert report["first_bad_seq"] == events[0]["seq"]
    # 首行 hash 未变 → 下游链接仍自洽，只改写断点行的 prev_hash（最小写入）
    assert report["repaired_count"] == 1
    assert updates == [{"seq": events[0]["seq"], "prev_hash": "", "hash": original_hash}]
    assert _verify_independent(_apply(events, updates), KEY)


def test_first_event_hash_tamper_restored_with_single_rewrite() -> None:
    events = _chain(["v2", "v1", "v2"])
    original_hash = events[0]["hash"]
    events[0]["prev_hash"] = "bogus"
    events[0]["hash"] = "v2:" + "0" * 64
    updates, report = recompute_chain(events, KEY)
    assert report["first_bad_seq"] == events[0]["seq"]
    # payload 未变 → 重算恢复原 hash，下游链接仍自洽，只改写首行
    assert report["repaired_count"] == 1
    assert updates == [{"seq": events[0]["seq"], "prev_hash": "", "hash": original_hash}]
    assert _verify_independent(_apply(events, updates), KEY)


def test_break_at_first_event_payload_tamper_propagates() -> None:
    events = _chain(["v2", "v1", "v2"])
    events[0]["prev_hash"] = "bogus"
    events[0]["payload"] = {**events[0]["payload"], "data": {"tampered": True}}
    updates, report = recompute_chain(events, KEY)
    assert report["first_bad_seq"] == events[0]["seq"]
    # 首行重算 hash 变化 → 逐条传播，整链重写且 scheme 跟随各行 stored hash
    assert report["repaired_count"] == 3
    assert updates[0]["prev_hash"] == ""
    assert updates[0]["hash"].startswith("v2:")
    assert not updates[1]["hash"].startswith("v2:")
    assert updates[2]["hash"].startswith("v2:")
    assert _verify_independent(_apply(events, updates), KEY)


def test_tampered_payload_detected_as_hash_mismatch() -> None:
    events = _chain(["v2", "v2", "v2", "v2"])
    events[1]["payload"] = {**events[1]["payload"], "data": {"tampered": True}}
    assert not _verify_independent(events, KEY)
    updates, report = recompute_chain(events, KEY)
    assert report["first_bad_seq"] == events[1]["seq"]
    assert report["missing_seqs"] == []
    assert report["repaired_count"] == 3
    assert _verify_independent(_apply(events, updates), KEY)


def test_seq_gaps_reported_without_breaking_healthy_chain() -> None:
    events = _chain(["v1", "v2", "v1", "v2", "v2"])
    for event, seq in zip(events, [1, 2, 5, 6, 9], strict=True):
        event["seq"] = seq
    assert _verify_independent(events, KEY)
    updates, report = recompute_chain(events, KEY)
    assert updates == []
    assert report["first_bad_seq"] is None
    assert report["missing_seqs"] == [3, 4, 7, 8]
    assert report["repaired_count"] == 0


def test_gap_and_break_combined() -> None:
    events = _chain(["v2", "v2", "v2", "v2", "v2"])
    for event, seq in zip(events, [10, 11, 15, 16, 20], strict=True):
        event["seq"] = seq
    survivors = [events[0], *events[2:]]  # 删除 seq=11
    updates, report = recompute_chain(survivors, KEY)
    assert report["missing_seqs"] == [11, 12, 13, 14, 17, 18, 19]
    assert report["first_bad_seq"] == 15
    assert report["repaired_count"] == 3
    assert _verify_independent(_apply(survivors, updates), KEY)