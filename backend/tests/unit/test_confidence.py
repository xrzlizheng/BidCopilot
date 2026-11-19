import pytest

from app.modules.generation.confidence import (
    LEVEL_AUTO,
    LEVEL_REQUIRED,
    LEVEL_SUGGESTED,
    grade_confidence,
    json_repair_signal,
    structure_signal,
)
from app.modules.generation.llm_client import LLMResponseError
from app.modules.generation.llm_json import complete_json_object_ex


class _FlakyClient:
    """按顺序吐预设输出，用于验证修复次数信号。"""

    model = "fake-judge"

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        output = self.outputs[self.calls]
        self.calls += 1
        return output


def test_grade_confidence_perfect_output_is_auto() -> None:
    grade = grade_confidence(
        repair_count=0, self_report="high", fields_present=1.0, has_evidence=True
    )

    assert grade.score == 1.0
    assert grade.level == LEVEL_AUTO
    assert grade.label == "high"
    assert grade.reason is None


def test_grade_confidence_single_repair_still_auto_but_downgrades_with_weak_self_report() -> None:
    clean = grade_confidence(
        repair_count=1, self_report="high", fields_present=1.0, has_evidence=True
    )
    assert clean.score == 0.88
    assert clean.level == LEVEL_AUTO
    assert clean.reason == "JSON 重试 1 次"

    # 修复一次 + 模型自评 medium：跌破自动化线。
    weak = grade_confidence(
        repair_count=1, self_report="medium", fields_present=1.0, has_evidence=True
    )
    assert weak.score == 0.82
    assert weak.level == LEVEL_SUGGESTED


def test_grade_confidence_two_repairs_forces_human_review() -> None:
    grade = grade_confidence(
        repair_count=2, self_report="high", fields_present=1.0, has_evidence=True
    )

    # 加权分 0.75 本来够 suggested，硬规则压到 0.69。
    assert grade.score == 0.69
    assert grade.level == LEVEL_REQUIRED
    assert grade.label == "low"
    assert "JSON 重试 2 次" in (grade.reason or "")


def test_grade_confidence_missing_fields_cannot_auto_pass() -> None:
    grade = grade_confidence(
        repair_count=0, self_report="high", fields_present=0.6, has_evidence=True
    )

    assert grade.score == 0.84
    assert grade.level == LEVEL_SUGGESTED
    assert "输出结构不完整" in (grade.reason or "")


def test_grade_confidence_low_self_report_cannot_auto_pass() -> None:
    grade = grade_confidence(
        repair_count=0, self_report="low", fields_present=1.0, has_evidence=True
    )

    assert grade.score == 0.84
    assert grade.level == LEVEL_SUGGESTED
    assert "模型自评置信度低" in (grade.reason or "")


def test_grade_confidence_without_evidence_only_loses_score() -> None:
    grade = grade_confidence(
        repair_count=0, self_report="high", fields_present=1.0, has_evidence=False
    )

    # 没有可引用章节是业务事实，不是输出坏了，只扣分不强制降级。
    assert grade.score == 0.97
    assert grade.level == LEVEL_AUTO
    assert grade.reason == "缺少证据片段"


def test_confidence_signals_are_monotonic() -> None:
    assert json_repair_signal(0) > json_repair_signal(1) > json_repair_signal(2)
    assert json_repair_signal(5) == json_repair_signal(2)
    assert structure_signal(1.0, has_evidence=True) > structure_signal(
        0.8, has_evidence=True
    )
    assert structure_signal(1.0, has_evidence=True) > structure_signal(
        1.0, has_evidence=False
    )


async def test_complete_json_object_ex_reports_zero_repair_on_first_try() -> None:
    client = _FlakyClient(['{"items": []}'])

    data, meta = await complete_json_object_ex(client, system="s", user="u", attempts=3)

    assert data == {"items": []}
    assert meta.attempts_used == 1
    assert meta.repair_count == 0
    assert meta.output_chars == len('{"items": []}')
    assert client.calls == 1


async def test_complete_json_object_ex_counts_repairs() -> None:
    client = _FlakyClient(["这不是 JSON", "```json\n{\"items\": [1]}\n```"])

    data, meta = await complete_json_object_ex(client, system="s", user="u", attempts=3)

    assert data == {"items": [1]}
    assert meta.attempts_used == 2
    assert meta.repair_count == 1
    assert client.calls == 2


async def test_complete_json_object_ex_raises_after_exhausting_attempts() -> None:
    client = _FlakyClient(["bad", "still bad"])

    with pytest.raises(LLMResponseError):
        await complete_json_object_ex(client, system="s", user="u", attempts=2)

    assert client.calls == 2
