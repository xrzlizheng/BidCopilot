"""把结构化调用的可观测信号折算成可解释的置信度与复核等级。

三个信号，按可靠性从高到低加权：

1. JSON 修复次数（权重 0.5）：模型第一次就输出合法结构，说明它对这段内容的结构化
   判断稳定；反复修复才成功，说明输出处在漂移边缘。这是唯一不依赖模型自我陈述的信号。
2. 结构完整性（权重 0.3）：约定字段是否齐全、是否给出证据片段。
3. 模型自评（权重 0.2）：high/medium/low，参考价值最低，只作微调。

除了加权，还有两条硬性降级规则，避免"底分太高"把问题盖掉：
- 契约字段不齐或模型自评 low：不允许进自动化通道（上限 0.84）；
- 同一 prompt 修了 2 次以上才拿到合法 JSON：直接判人工复核（上限 0.69）。

**阈值与权重是保守默认值，尚未在本项目真实标书数据上校准。** 置信度分数不等于
"准确率"，0.9 不代表 90% 正确；上线前必须用真实样本核对每个等级的实际误判率，
再决定是否收紧 AUTO_THRESHOLD。
"""

from __future__ import annotations

from dataclasses import dataclass

# 自动化 / 建议复核 / 强制复核 的分界。评标直接影响投标决策，默认值取得比通用场景更保守。
AUTO_THRESHOLD = 0.85
SUGGESTED_THRESHOLD = 0.70

WEIGHT_JSON_REPAIR = 0.5
WEIGHT_STRUCTURE = 0.3
WEIGHT_SELF_REPORT = 0.2

SELF_REPORT_SCORES = {"high": 1.0, "medium": 0.7, "low": 0.4}

LEVEL_AUTO = "auto"
LEVEL_SUGGESTED = "suggested"
LEVEL_REQUIRED = "required"

# 对外仍用 high/medium/low 三档，保持既有字段语义可读。
LEVELS_TO_LABEL = {
    LEVEL_AUTO: "high",
    LEVEL_SUGGESTED: "medium",
    LEVEL_REQUIRED: "low",
}


@dataclass(frozen=True)
class ConfidenceGrade:
    score: float
    level: str
    label: str
    reason: str | None


def json_repair_signal(repair_count: int) -> float:
    """修复次数越少越可信。"""
    if repair_count <= 0:
        return 1.0
    if repair_count == 1:
        return 0.75
    return 0.5


def structure_signal(fields_present: float, *, has_evidence: bool) -> float:
    """字段齐全度用阶梯而非线性：缺一个字段和缺三个字段的性质不同。

    证据缺席按 0.9 折算而不是 0：某些评分项确实无可引用章节（章节尚未生成），
    这是业务事实，不等于模型输出坏了，两者不能同等降级。
    """
    if fields_present >= 1.0:
        base = 1.0
    elif fields_present >= 0.8:
        base = 0.85
    elif fields_present >= 0.6:
        base = 0.65
    else:
        base = 0.4
    return base * (1.0 if has_evidence else 0.9)


def grade_confidence(
    *,
    repair_count: int,
    self_report: str,
    fields_present: float,
    has_evidence: bool,
) -> ConfidenceGrade:
    repair = json_repair_signal(repair_count)
    structure = structure_signal(fields_present, has_evidence=has_evidence)
    self_score = SELF_REPORT_SCORES.get(self_report, SELF_REPORT_SCORES["low"])
    score = (
        WEIGHT_JSON_REPAIR * repair
        + WEIGHT_STRUCTURE * structure
        + WEIGHT_SELF_REPORT * self_score
    )
    reasons: list[str] = []
    if repair_count > 0:
        reasons.append(f"JSON 重试 {repair_count} 次")
    if fields_present < 1.0:
        reasons.append("输出结构不完整")
    elif not has_evidence:
        reasons.append("缺少证据片段")
    if self_score <= SELF_REPORT_SCORES["low"]:
        reasons.append("模型自评置信度低")
    # 硬性下调 1：契约字段不齐、或模型自己都说不确定，不许进自动化通道。
    if fields_present < 1.0 or self_score <= SELF_REPORT_SCORES["low"]:
        score = min(score, AUTO_THRESHOLD - 0.01)
    # 硬性下调 2：反复修 JSON 才成功，说明这次结构化判断本身在漂移。
    if repair_count >= 2:
        score = min(score, SUGGESTED_THRESHOLD - 0.01)
    score = round(max(0.0, min(1.0, score)), 2)
    if score >= AUTO_THRESHOLD:
        level = LEVEL_AUTO
    elif score >= SUGGESTED_THRESHOLD:
        level = LEVEL_SUGGESTED
    else:
        level = LEVEL_REQUIRED
    return ConfidenceGrade(
        score=score,
        level=level,
        label=LEVELS_TO_LABEL[level],
        reason="；".join(reasons) if reasons else None,
    )
