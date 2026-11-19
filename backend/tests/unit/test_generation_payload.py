import json
from decimal import Decimal

from app.modules.generation.service import _json_payload


def test_decimal_in_prompt_payload_is_serialized_not_crashing() -> None:
    """真实生成曾因评分/预算的 Decimal 直接抛 TypeError 返回 500。"""
    payload = {
        "scoring_items": [{"item": "技术方案", "score": Decimal("30")}],
        "budget": Decimal("1980000.00"),
        "project": {"name": "某市政务云"},
    }
    decoded = json.loads(_json_payload(payload))
    assert decoded["scoring_items"][0]["score"] == "30"
    assert decoded["budget"] == "1980000.00"
    assert decoded["project"]["name"] == "某市政务云"
