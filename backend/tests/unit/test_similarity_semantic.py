"""跨投标人语义级相似度（`app.modules.file_check.semantic`）的确定性单测。

样本全部为自建真实中文文本（技术标常见表述 + 无关领域表述），不引用外部语料。

覆盖任务要求：
- 同义改写 → 高语义分、低字面分；
- 无关文本 → 低分；
- 确定性：同输入两次结果完全相同；
- 空输入 / 过短输入 / 超长输入边界；
- 证据链：共同概念、共同术语、各层贡献之和等于总分、分层计数；
- 能力边界：不出现「串标」定性，不提供规避建议。
"""

from __future__ import annotations

import json

import pytest

from app.modules.file_check.semantic import (
    CONCEPT_WEIGHT,
    MAX_COMPARE_CHARS,
    MAX_TOTAL_COMPARE_CHARS,
    NGRAM_MAX_UNIQUE,
    NGRAM_WEIGHT,
    TERM_WEIGHT,
    FileCheckLimitError,
    combined_score,
    compare_prepared,
    compare_semantic,
    prepare_document,
    readable_text,
    semantic_level,
)
from app.modules.file_check.similarity import compare as lexical_compare

# ---------------------------------------------------------------------------
# 自建样本：同一件事的两种写法（同义改写）与两个无关领域
# ---------------------------------------------------------------------------

PARAPHRASE_LEFT = (
    "我方技术方案围绕系统架构、实施计划、项目团队、质量管理、进度计划、培训与验收展开："
    "实施阶段按周跟踪进度，质量保证覆盖全过程，项目团队配置项目经理与工程师，"
    "交付前完成系统测试与培训，验收后提供运维服务与售后服务，服务响应时间不超过两小时。"
)

PARAPHRASE_RIGHT = (
    "我司技术建议书涵盖总体架构、实施安排、项目班子、质量保证体系、工期安排、技术培训与竣工验收："
    "实施过程中按周核对工期，质量控制贯穿全流程，项目组由项目经理及工程师组成，"
    "交付前完成联调测试与操作培训，竣工验收后提供运行维护与质保服务，故障响应时长两小时内。"
)

UNRELATED_FURNITURE = (
    "本项目为办公家具采购，包含实木桌椅、文件柜与会议桌，要求环保等级达到E1，"
    "供货期三十日，送货上门并负责现场安装，质保期内免费维修。"
)

UNRELATED_CATERING = (
    "食堂食材配送服务，每日供应蔬菜、鲜肉与米面粮油，需全程冷链运输，"
    "随货附检验检疫证明，遇节假日需提前一日确认供货量。"
)

COPY_PASTE = (
    "本项目技术方案包括系统架构设计、实施计划、人员配置与运维服务，"
    "提供7x24小时响应，确保按期交付。"
)


def test_paraphrase_scores_high_semantic_but_low_lexical() -> None:
    """核心诉求：同义改写在语义层得高分，但在旧的字面层几乎为 0。"""
    semantic = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)
    lexical = lexical_compare(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)

    assert semantic["semantic_score"] >= 0.6
    assert semantic["semantic_level"] in {"medium", "high"}
    # 字面分必须显著低于语义分——否则说明这层没有补上「换个说法」的盲区
    assert lexical["score"] < 0.2
    assert semantic["semantic_score"] - lexical["score"] > 0.4


def test_paraphrase_evidence_names_shared_concepts_with_both_surface_forms() -> None:
    """依据链必须能指出：哪些概念命中、两侧各自用什么词表达的。"""
    evidence = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)["semantic_evidence"]

    assert evidence["shared_concepts"]
    pairs = {item["concept"]: item for item in evidence["concept_pairs"]}
    assert "技术方案" in pairs
    # 同一概念、两种写法：左侧「技术方案」对右侧「技术建议书」
    assert "技术方案" in pairs["技术方案"]["left"]
    assert "技术建议书" in pairs["技术方案"]["right"]
    assert "实施方案" in pairs
    assert {"实施计划", "实施安排"} & set(pairs["实施方案"]["left"])
    # 术语层有重合，说明「专业语汇」也一致（与概念层互相印证）
    assert evidence["shared_terms"]


def test_unrelated_documents_score_low() -> None:
    """无关文本：语义分与字面分都低，等级为 low。"""
    pairs = [
        (PARAPHRASE_LEFT, UNRELATED_FURNITURE),
        (PARAPHRASE_LEFT, UNRELATED_CATERING),
        (UNRELATED_FURNITURE, UNRELATED_CATERING),
    ]
    for left, right in pairs:
        result = compare_semantic(left, right)
        assert result["semantic_score"] < 0.3
        assert result["semantic_level"] == "low"
        assert lexical_compare(left, right)["score"] < 0.1


def test_identical_documents_score_one() -> None:
    result = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_LEFT)
    assert result["semantic_score"] == 1.0
    assert result["semantic_level"] == "high"
    assert lexical_compare(PARAPHRASE_LEFT, PARAPHRASE_LEFT)["score"] == 1.0


def test_copy_paste_is_high_lexical_and_high_semantic() -> None:
    """完全相同的短文本（旧接口的主要场景）仍要被判为 high，不能因语义层引入而回退。"""
    result = compare_semantic(COPY_PASTE, COPY_PASTE)
    assert result["semantic_score"] == 1.0
    assert result["semantic_level"] == "high"
    assert lexical_compare(COPY_PASTE, COPY_PASTE)["level"] == "high"


def test_deterministic_across_repeated_calls_and_key_order() -> None:
    """同输入两次结果完全相同（含证据链与浮点位），且与 JSON 键顺序无关。"""
    first = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)
    second = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["semantic_evidence"] == second["semantic_evidence"]


def test_prepare_document_is_reusable_and_order_independent() -> None:
    """画像复用不改变结果；参数顺序互换后分值相同（向量比较对称）。"""
    left = prepare_document(PARAPHRASE_LEFT)
    right = prepare_document(PARAPHRASE_RIGHT)

    forward = compare_prepared(left, right)
    backward = compare_prepared(right, left)

    assert forward["semantic_score"] == backward["semantic_score"]
    assert forward["semantic_level"] == backward["semantic_level"]


def test_evidence_contributions_sum_to_score() -> None:
    """分值来源可核验：三层贡献之和必须等于总分（允许 4 位小数的舍入误差）。"""
    result = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)
    evidence = result["semantic_evidence"]
    contributions = evidence["contributions"]

    assert contributions["concept"]["weight"] == CONCEPT_WEIGHT
    assert contributions["ngram"]["weight"] == NGRAM_WEIGHT
    assert contributions["term"]["weight"] == TERM_WEIGHT
    assert round(CONCEPT_WEIGHT + NGRAM_WEIGHT + TERM_WEIGHT, 6) == 1.0

    expected = round(
        contributions["concept"]["raw"] * CONCEPT_WEIGHT
        + contributions["ngram"]["raw"] * NGRAM_WEIGHT
        + contributions["term"]["raw"] * TERM_WEIGHT,
        4,
    )
    # 三层 raw 各自先 round 到 4 位再相乘，与直接用未舍入值求总分最多差一个末位
    assert abs(expected - result["semantic_score"]) <= 2e-4
    assert evidence["formula"]
    assert evidence["explanation"]


def test_evidence_counts_are_consistent() -> None:
    evidence = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)["semantic_evidence"]
    counts = evidence["layer_counts"]

    assert counts["concept_shared"] == len(evidence["shared_concepts"])
    assert counts["term_shared"] == len(evidence["shared_terms"])
    assert counts["ngram_shared"] <= counts["ngram_union"]
    assert counts["term_shared"] <= counts["term_union"]
    assert len(evidence["shared_concepts"]) == len(evidence["concept_pairs"])


def test_shared_excerpts_only_for_similar_pairs() -> None:
    """共同片段只展示字面真实存在的连续片段，且仅在需要复核的等级下给出。"""
    similar = compare_semantic(COPY_PASTE, COPY_PASTE)["semantic_evidence"]
    assert similar["shared_excerpts"]
    readable = readable_text(COPY_PASTE)
    assert all(fragment in readable for fragment in similar["shared_excerpts"])

    unrelated = compare_semantic(PARAPHRASE_LEFT, UNRELATED_CATERING)["semantic_evidence"]
    assert unrelated["shared_excerpts"] == []


def test_empty_input_scores_zero_with_note() -> None:
    for left, right in (("", ""), ("", PARAPHRASE_LEFT), (PARAPHRASE_LEFT, "")):
        result = compare_semantic(left, right)
        assert result["semantic_score"] == 0.0
        assert result["semantic_level"] == "low"
        assert result["semantic_evidence"]["note"]
        assert result["semantic_evidence"]["shared_concepts"] == []


def test_too_short_input_scores_zero_with_note() -> None:
    """过短文本（少于 8 个有效字符）不做语义比较，明确给 0 并说明原因。"""
    result = compare_semantic("同意", "同意")
    assert result["semantic_score"] == 0.0
    assert result["semantic_level"] == "low"
    assert "过短" in result["semantic_evidence"]["note"]


def test_boundary_text_is_compared_not_rejected() -> None:
    """刚好达到最小长度时正常比较（边界值不被误判为过短）。"""
    text = "技术方案包含系统架构"  # 10 个有效字符
    result = compare_semantic(text, text)
    assert result["semantic_score"] == 1.0
    assert "note" not in result["semantic_evidence"]


def test_oversized_document_raises_explicit_limit_error() -> None:
    """超长正文抛 400 异常，不静默截断。"""
    with pytest.raises(FileCheckLimitError) as excinfo:
        prepare_document("字" * (MAX_COMPARE_CHARS + 1))
    error = excinfo.value
    assert error.status_code == 400
    assert error.code == "file_check_limit_exceeded"
    assert str(MAX_COMPARE_CHARS) in error.message


def test_at_limit_document_is_accepted() -> None:
    profile = prepare_document("字" * MAX_COMPARE_CHARS)
    assert profile.chars == MAX_COMPARE_CHARS
    assert len(profile.grams) <= NGRAM_MAX_UNIQUE


def test_total_budget_constant_covers_ten_max_size_files() -> None:
    """性能护栏自检：总预算必须覆盖「10 份中等文件」，且小于「10 份极限文件」。"""
    assert MAX_TOTAL_COMPARE_CHARS >= 10 * 50_000
    assert MAX_TOTAL_COMPARE_CHARS < 10 * MAX_COMPARE_CHARS


def test_long_text_gram_sampling_is_deterministic() -> None:
    """超长文本触发 bottom-k 抽样时仍是确定值。"""
    long_text = PARAPHRASE_LEFT * 400
    first = prepare_document(long_text)
    second = prepare_document(long_text)
    assert len(long_text) > 2 * NGRAM_MAX_UNIQUE
    assert len(first.grams) <= NGRAM_MAX_UNIQUE
    assert first.grams == second.grams
    assert compare_prepared(first, second)["semantic_score"] == 1.0


def test_semantic_level_thresholds() -> None:
    assert semantic_level(0.0) == "low"
    assert semantic_level(0.4999) == "low"
    assert semantic_level(0.5) == "medium"
    assert semantic_level(0.7499) == "medium"
    assert semantic_level(0.75) == "high"
    assert semantic_level(1.0) == "high"


def test_combined_score_is_deterministic_weighted_sum() -> None:
    assert combined_score(1.0, 1.0) == 1.0
    assert combined_score(0.0, 0.0) == 0.0
    assert combined_score(0.4, 0.8) == combined_score(0.4, 0.8) == 0.6
    # 组合分仍落在 0–1，排序方向与旧 score 一致（越大越需复核）
    assert 0.0 <= combined_score(0.13, 0.87) <= 1.0


def test_scores_stay_within_unit_interval() -> None:
    cases = [
        (PARAPHRASE_LEFT, PARAPHRASE_RIGHT),
        (COPY_PASTE, PARAPHRASE_LEFT),
        (UNRELATED_FURNITURE, UNRELATED_CATERING),
    ]
    for left, right in cases:
        score = compare_semantic(left, right)["semantic_score"]
        assert 0.0 <= score <= 1.0


def test_no_disparagement_or_evasion_advice_in_output() -> None:
    """能力边界：输出不得出现「串标」定性，也不得给规避建议。"""
    result = compare_semantic(PARAPHRASE_LEFT, PARAPHRASE_RIGHT)
    payload = json.dumps(result, ensure_ascii=False)
    evidence = result["semantic_evidence"]

    assert "不构成「串标」认定" in evidence["boundary"]
    assert "串通投标" not in payload
    # 边界句里出现的「规避」只能是「不提供…建议」这个否定式，不能是可执行的建议
    assert "不提供任何规避检测建议" in payload
    for banned in ("如何避免", "避免被查", "规避查重", "应对查重", "降低相似度", "改写技巧"):
        assert banned not in payload
    assert "合规自查" in evidence["boundary"]
    assert evidence["boundary"] == (
        "仅用于跨投标人合规自查与人工复核，不构成「串标」认定，不提供任何规避检测建议。"
    )
