import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.evaluation import service as evaluation_service
from app.modules.evaluation.service import (
    EvaluationResultError,
    build_prompt,
    extract_json_object,
    normalize_result,
)


def _item(
    item_id: str,
    title: str,
    score: str = "20",
    category: str = "技术",
    criteria: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        category=category,
        item=title,
        score=Decimal(score),
        criteria=criteria,
    )


def _section(
    section_id: str,
    title: str,
    text: str,
    scoring_item_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=section_id,
        title=title,
        status="draft",
        scoring_item_id=scoring_item_id,
        content_text=text,
        order_index=0,
    )


def _project() -> SimpleNamespace:
    return SimpleNamespace(name="智慧城市项目", buyer="某市大数据局")


def _raw_item(item_id: str, score: float, *, needs_review: bool = False) -> dict:
    return {
        "scoring_item_id": item_id,
        "score": score,
        "rationale": "覆盖评分点",
        "evidence": ["章节正文"],
        "gaps": [],
        "confidence": "high",
        "needs_review": needs_review,
    }


def test_extract_json_object_handles_code_fence() -> None:
    content = '```json\n{"items": [], "overall_summary": "ok"}\n```'

    data = extract_json_object(content)

    assert data["overall_summary"] == "ok"


def test_extract_json_object_rejects_invalid_payload() -> None:
    with pytest.raises(EvaluationResultError):
        extract_json_object("not json")


def test_normalize_result_clamps_scores_and_marks_missing_unevaluated() -> None:
    first_id = "11111111-1111-1111-1111-111111111111"
    second_id = "22222222-2222-2222-2222-222222222222"
    items = [_item(first_id, "技术方案"), _item(second_id, "项目业绩", "10")]
    raw = {
        "items": [
            {
                "scoring_item_id": first_id,
                "score": 99,
                "rationale": "方案完整",
                "evidence": ["章节：技术方案响应"],
                "gaps": [],
                "confidence": "high",
                "needs_review": False,
            },
            {
                "scoring_item_id": "unknown",
                "score": 5,
            },
        ],
        "overall_summary": "整体可投",
        "risks": ["业绩证明不足"],
        "improvements": ["补充合同扫描件"],
    }

    result = normalize_result(raw, items, meta={"items": 2})

    # 漏评项不进分母：已评部分满分就该是 100%，而不是被漏评项拉低到 66.7%。
    assert result["summary"]["total_score"] == 20.0
    assert result["summary"]["max_score"] == 20.0
    assert result["summary"]["percentage"] == 100.0
    assert result["summary"]["band"] == "strong"
    # 覆盖不全时评级只是"已评部分"的结论。
    assert result["summary"]["band_status"] == "provisional"
    assert result["summary"]["expected_max_score"] == 30.0
    assert result["summary"]["coverage"] == {
        "evaluated_items": 1,
        "expected_items": 2,
        "ratio": 0.5,
    }
    assert result["summary"]["unevaluated_count"] == 1
    assert [entry["scoring_item_id"] for entry in result["summary"]["incomplete_items"]] == [
        second_id
    ]
    # needs_review_count 只数已评项，漏评项由 incomplete_items 单独表达。
    assert result["summary"]["needs_review_count"] == 0
    assert result["items"][0]["score"] == 20.0
    assert result["items"][0]["evaluated"] is True
    # 未评估是 None，不是 0.0 —— 前端必须能区分"没评"和"评了得零分"。
    assert result["items"][1]["score"] is None
    assert result["items"][1]["evaluated"] is False
    assert result["items"][1]["needs_review"] is True
    assert any("未知评分项" in warning for warning in result["warnings"])
    assert any("模型漏评" in warning for warning in result["warnings"])


def test_normalize_result_full_coverage_keeps_band_final() -> None:
    first_id = "aaaaaaaa-0000-0000-0000-000000000001"
    second_id = "aaaaaaaa-0000-0000-0000-000000000002"
    items = [_item(first_id, "技术方案"), _item(second_id, "项目业绩", "10")]
    raw = {"items": [_raw_item(first_id, 16), _raw_item(second_id, 6)]}

    result = normalize_result(raw, items, meta={"items": 2})

    assert result["summary"]["total_score"] == 22.0
    assert result["summary"]["max_score"] == 30.0
    assert result["summary"]["percentage"] == 73.3
    assert result["summary"]["band"] == "competitive"
    assert result["summary"]["band_status"] == "final"
    assert result["summary"]["coverage"]["ratio"] == 1.0
    assert result["summary"]["incomplete_items"] == []
    assert result["summary"]["unevaluated_count"] == 0


def test_normalize_result_forces_review_when_item_has_no_evidence() -> None:
    item_id = "bbbbbbbb-0000-0000-0000-000000000001"
    items = [_item(item_id, "技术方案")]
    raw = {"items": [_raw_item(item_id, 18, needs_review=False)]}
    meta = {"items": 1, "no_evidence_item_ids": [item_id]}

    result = normalize_result(raw, items, meta=meta)

    entry = result["items"][0]
    # 没有证据还给出分数等于猜测，模型自评"无需复核"不成立。
    assert entry["needs_review"] is True
    assert any("未检索到相关证据段落" in gap for gap in entry["gaps"])
    assert any("未检索到相关证据段落" in warning for warning in result["warnings"])


def test_build_prompt_prefers_bound_section_even_without_keyword_overlap() -> None:
    """同义表述的根治：绑定章节整章送，不依赖任何词面重合。"""
    item_id = "cccccccc-0000-0000-0000-000000000001"
    sections = [
        _section(
            "s1",
            "zzzz性能指标响应",
            "系统响应时间不超过二百毫秒，支持一千并发连接。\n"
            "数据库主从切换时间控制在三十秒内。",
            item_id,
        )
    ]
    # 评分项文本与章节正文没有任何 bigram 重合，关键词召回必然为空。
    items = [_item(item_id, "qqqq")]

    payload, meta = build_prompt(_project(), items, sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert entry["evidence_source"] == "bound_full"
    assert any("二百毫秒" in item["text"] for item in entry["evidence"])
    assert any("三十秒" in item["text"] for item in entry["evidence"])
    assert meta["items_bound_full"] == 1
    assert meta["items_without_evidence"] == 0


def test_build_prompt_keeps_whole_bound_section_when_within_budget() -> None:
    item_id = "cccccccc-0000-0000-0000-000000000002"
    body = "技术方案总体设计说明。\n实施计划分三个阶段推进。\n保障措施包括人员与进度安排。"
    sections = [_section("s1", "技术方案响应", body, item_id)]

    payload, meta = build_prompt(_project(), [_item(item_id, "技术方案")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert entry["evidence_source"] == "bound_full"
    # 整章都在，一个字都没丢。
    assert "".join(item["text"] for item in entry["evidence"]).replace("\n", "") == body.replace(
        "\n", ""
    )
    assert meta["truncated"] is False


def test_build_prompt_trims_overlong_bound_section_keeping_head_and_tail() -> None:
    item_id = "cccccccc-0000-0000-0000-000000000003"
    lines = [f"技术方案第{index}段：{'填充论证内容' * 60}。" for index in range(10)]
    sections = [_section("s1", "技术方案响应", "\n".join(lines), item_id)]

    payload, meta = build_prompt(_project(), [_item(item_id, "技术方案")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert entry["evidence_source"] == "bound_trimmed"
    # 超预算时保头尾：开头是概述、结尾是承诺，不会被"只取前 N 字"砍掉结论。
    assert any("第0段" in item["text"] for item in entry["evidence"])
    assert any("第9段" in item["text"] for item in entry["evidence"])
    assert sum(len(item["text"]) for item in entry["evidence"]) <= 2000
    assert meta["items_bound_trimmed"] == 1
    assert meta["truncated"] is True


def test_build_prompt_supplements_with_cross_section_recall() -> None:
    item_id = "cccccccc-0000-0000-0000-000000000004"
    sections = [
        _section("s1", "技术方案响应", "技术方案采用微服务架构，支持弹性扩容。", item_id),
        _section("s2", "商务业绩材料", "近三年完成政务云平台项目两个，合同额一千二百万元。"),
    ]
    items = [_item(item_id, "技术方案与项目业绩")]

    payload, meta = build_prompt(_project(), items, sections)

    entry = json.loads(payload)["scoring_items"][0]
    texts = " ".join(item["text"] for item in entry["evidence"])
    # 主证据来自绑定章节，跨章节召回只作补充（业绩常写在固定章节里）。
    assert entry["evidence_source"] == "bound_full"
    assert "微服务" in texts
    assert "政务云" in texts


def test_build_prompt_marks_item_without_any_evidence() -> None:
    item_id = "eeeeeeee-0000-0000-0000-000000000001"
    sections = [_section("s1", "其他章节", "甲甲公司乙乙内容。")]

    payload, meta = build_prompt(_project(), [_item(item_id, "qqqq")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert entry["evidence"] == []
    assert entry["evidence_source"] == "none"
    assert meta["items_without_evidence"] == 1
    assert meta["no_evidence_item_ids"] == [item_id]


def test_build_prompt_respects_total_char_budget(monkeypatch) -> None:
    monkeypatch.setattr(evaluation_service, "MAX_TOTAL_CHARS", 30)
    item_id = "ffffffff-1111-0000-0000-000000000001"
    sections = [_section("s1", "技术方案响应", "技术方案内容" * 20, item_id)]

    payload, meta = build_prompt(_project(), [_item(item_id, "技术方案")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert entry["evidence_source"] == "bound_trimmed"
    assert meta["truncated"] is True
    assert sum(len(item["text"]) for item in entry["evidence"]) <= 30


def test_build_prompt_includes_response_matrix_hints() -> None:
    item_id = "a1a1a1a1-0000-0000-0000-000000000001"
    sections = [_section("s1", "类似项目业绩响应", "近三年完成政务云项目两个。", item_id)]
    matrix = {
        item_id: {
            "evidence_required": ["业绩", "验收报告"],
            "missing_evidence": ["验收报告"],
            "coverage_status": "partial",
        }
    }

    payload, meta = build_prompt(
        _project(), [_item(item_id, "类似项目业绩", criteria="每提供1个合同得2分")], sections,
        matrix=matrix,
    )

    entry = json.loads(payload)["scoring_items"][0]
    # 评标要像标书专家复核那样：知道这项该核查什么、准备阶段已知缺什么。
    assert entry["item_type"] == "objective"
    assert entry["expected_evidence"] == ["业绩", "验收报告"]
    assert entry["declared_missing"] == ["验收报告"]
    assert entry["coverage_status"] == "partial"
    assert meta["items_with_declared_gap"] == 1


def test_build_prompt_omits_matrix_fields_when_absent() -> None:
    item_id = "a1a1a1a1-0000-0000-0000-000000000002"
    sections = [_section("s1", "技术方案响应", "技术方案采用微服务架构。", item_id)]

    payload, meta = build_prompt(_project(), [_item(item_id, "技术方案")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    # 没有矩阵时不能塞 null 字段，prompt 要保持干净。
    assert "expected_evidence" not in entry
    assert "declared_missing" not in entry
    assert meta["items_with_declared_gap"] == 0


def test_build_prompt_flags_unverifiable_references() -> None:
    item_id = "a1a1a1a1-0000-0000-0000-000000000003"
    sections = [
        _section(
            "s1",
            "资质材料响应",
            "我方具备系统集成一级资质，证书详见附件5。验收报告参见第9章。",
            item_id,
        )
    ]

    payload, meta = build_prompt(_project(), [_item(item_id, "资质材料")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    # "详见附件5"不等于附件已提供，必须让模型知道这条引用核实不了。
    targets = {reference["target"] for reference in entry["unverifiable_references"]}
    assert "附件5" in targets
    assert "9" in targets
    assert meta["items_with_unresolved_refs"] == 1


def test_build_prompt_omits_reference_field_when_none() -> None:
    item_id = "a1a1a1a1-0000-0000-0000-000000000004"
    sections = [_section("s1", "技术方案响应", "技术方案采用微服务架构，支持弹性扩容。", item_id)]

    payload, meta = build_prompt(_project(), [_item(item_id, "技术方案")], sections)

    entry = json.loads(payload)["scoring_items"][0]
    assert "unverifiable_references" not in entry
    assert meta["items_with_unresolved_refs"] == 0


def test_normalize_result_flags_matrix_conflict_on_high_score() -> None:
    item_id = "b2b2b2b2-0000-0000-0000-000000000001"
    raw = {"items": [_raw_item(item_id, 20, needs_review=False)]}
    hints = {item_id: {"missing_evidence": ["验收报告"], "coverage_status": "partial"}}

    result = normalize_result(
        raw, [_item(item_id, "类似项目业绩")], meta={"items": 1}, matrix_hints=hints
    )

    entry = result["items"][0]
    # 矩阵说缺验收报告、评标却给满分：必须转人工核对。
    assert entry["matrix_conflict"] and "验收报告" in entry["matrix_conflict"]
    assert entry["needs_review"] is True
    assert any("验收报告" in gap for gap in entry["gaps"])
    assert result["summary"]["matrix_conflict_count"] == 1


def test_normalize_result_flags_matrix_conflict_on_missing_coverage() -> None:
    item_id = "b2b2b2b2-0000-0000-0000-000000000002"
    raw = {"items": [_raw_item(item_id, 5, needs_review=False)]}
    hints = {item_id: {"coverage_status": "missing"}}

    result = normalize_result(
        raw, [_item(item_id, "资质证书")], meta={"items": 1}, matrix_hints=hints
    )

    assert result["items"][0]["matrix_conflict"]
    assert result["summary"]["matrix_conflict_count"] == 1


def test_normalize_result_does_not_flag_matrix_when_score_is_low() -> None:
    item_id = "b2b2b2b2-0000-0000-0000-000000000003"
    raw = {"items": [_raw_item(item_id, 3, needs_review=False)]}
    hints = {item_id: {"missing_evidence": ["验收报告"], "coverage_status": "partial"}}

    result = normalize_result(
        raw, [_item(item_id, "类似项目业绩")], meta={"items": 1}, matrix_hints=hints
    )

    # 矩阵说缺、评标也确实给了低分：这是正常的，不该误报矛盾。
    assert result["items"][0]["matrix_conflict"] is None
    assert result["summary"]["matrix_conflict_count"] == 0


def test_normalize_result_rejects_no_valid_items() -> None:
    with pytest.raises(EvaluationResultError):
        normalize_result({"items": [{"scoring_item_id": "unknown", "score": 1}]}, [], meta={})


def test_normalize_result_escalates_review_when_json_was_repaired() -> None:
    item_id = "ffffffff-0000-0000-0000-000000000001"
    items = [_item(item_id, "技术方案")]
    raw = {"items": [_raw_item(item_id, 18, needs_review=False)]}

    clean = normalize_result(raw, items, meta={"items": 1, "json_repair_count": 0})
    clean_item = clean["items"][0]
    # 首次就拿到合法 JSON + 字段齐全 + 自评 high：唯一可以走自动通道的情形。
    assert clean_item["review_level"] == "auto"
    assert clean_item["confidence_score"] == 1.0
    assert clean_item["needs_review"] is False
    assert clean_item["model_confidence"] == "high"
    assert clean["summary"]["confidence_summary"] == {
        "auto": 1,
        "suggested": 0,
        "required": 0,
    }
    assert clean["summary"]["json_repair_count"] == 0

    repaired = normalize_result(raw, items, meta={"items": 1, "json_repair_count": 2})
    repaired_item = repaired["items"][0]
    # 模型自己说不用复核也没用：反复修 JSON 才成功，一律人工复核。
    assert repaired_item["review_level"] == "required"
    assert repaired_item["needs_review"] is True
    assert repaired_item["confidence_score"] == 0.69
    assert "JSON 重试 2 次" in (repaired_item["review_reason"] or "")
    assert repaired["summary"]["confidence_summary"] == {
        "auto": 0,
        "suggested": 0,
        "required": 1,
    }
    assert repaired["summary"]["min_confidence"] == 0.69


def test_normalize_result_keeps_unevaluated_items_out_of_confidence_summary() -> None:
    first_id = "eeeeeeee-1111-0000-0000-000000000001"
    second_id = "eeeeeeee-1111-0000-0000-000000000002"
    items = [_item(first_id, "技术方案"), _item(second_id, "项目业绩", "10")]
    raw = {"items": [_raw_item(first_id, 18, needs_review=False)]}

    result = normalize_result(raw, items, meta={"items": 2, "json_repair_count": 0})

    # 漏评项没有置信度可言，不能混进分布里稀释整体可信度。
    assert result["summary"]["confidence_summary"] == {
        "auto": 1,
        "suggested": 0,
        "required": 0,
    }
    assert result["summary"]["min_confidence"] == 1.0
    assert result["items"][1]["confidence_score"] is None
    assert result["items"][1]["review_level"] is None
