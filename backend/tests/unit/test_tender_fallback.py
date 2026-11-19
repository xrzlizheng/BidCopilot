"""双层兜底抽取（规则→UIE→LLM）的单元锁定：开关关闭零调用、证据必带、失败降级。"""

import json

from app.modules.tender.fallback import (
    LLM_SYSTEM_PROMPT,
    UieExtractor,
    apply_fallback,
    candidate_blocks,
    detect_deficiency,
    llm_extract,
    uie_available,
)
from app.modules.tender.parser import Block, ParsedDocument


def _parsed(*lines: str) -> ParsedDocument:
    return ParsedDocument(
        blocks=[Block(index=i, text=text, kind="paragraph", page=1) for i, text in enumerate(lines)]
    )


def _rich_payload() -> dict:
    return {
        "scoring_items": [
            {"item": "技术方案", "score": 30.0, "confidence": "high"}
        ],
        "qualification_requirements": [
            {"text": "须具备涉密资质", "confidence": "medium"}
        ],
        "reject_clauses": [{"text": "串通投标的其投标无效", "confidence": "high"}],
    }


def test_detect_deficiency_rules_and_low_confidence() -> None:
    assert detect_deficiency(_rich_payload()) == []

    empty = {
        "scoring_items": [],
        "qualification_requirements": [],
        "reject_clauses": [],
    }
    dims = [item.dimension for item in detect_deficiency(empty)]
    assert dims == ["scoring", "qualification", "reject"]

    low_conf = {
        "scoring_items": [{"item": "方案", "score": 10.0, "confidence": "medium"}],
        "qualification_requirements": [],
        "reject_clauses": [],
    }
    dims = [item.dimension for item in detect_deficiency(low_conf)]
    assert dims == ["scoring", "qualification", "reject"]


def test_candidate_blocks_filter_by_cues_and_cap() -> None:
    parsed = _parsed(
        "项目概述文字",
        "技术方案 30 分",
        "投标人须具备信息系统集成资质",
        "串通投标的，其投标无效",
        "与线索无关的行",
    )
    picked = candidate_blocks(parsed, ["scoring", "qualification", "reject"])
    texts = [block.text for _, block in picked]
    assert "技术方案 30 分" in texts
    assert "投标人须具备信息系统集成资质" in texts
    assert "串通投标的，其投标无效" in texts
    assert "与线索无关的行" not in texts

    many = _parsed(*[f"评分项候选第{i}行" for i in range(100)])
    assert len(candidate_blocks(many, ["scoring"])) == 60


def test_uie_available_is_bool_without_heavy_import() -> None:
    assert isinstance(uie_available(), bool)


class _FakeTaskflow:
    def __init__(self, results):
        self._results = results
        self.calls: list[list[str]] = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return self._results


async def test_uie_extractor_maps_spans_to_dimensions() -> None:
    results = [
        {
            "评分项": [{"text": "运维服务能力", "probability": 0.92}],
            "资格要求": [],
            "废标条款": [],
        }
    ]
    taskflow = _FakeTaskflow(results)
    extractor = UieExtractor(taskflow_factory=lambda: taskflow)
    parsed = _parsed("评分办法：运维服务能力 30 分")
    candidates = candidate_blocks(parsed, ["scoring"])

    collected = await extractor.extract(candidates)

    assert collected["scoring"][0]["text"] == "运维服务能力"
    assert collected["scoring"][0]["page"] == 1
    assert taskflow.calls and taskflow.calls[0][0].startswith("评分办法")


async def test_apply_fallback_both_disabled_makes_zero_calls() -> None:
    class _Boom:
        def __init__(self):
            self.calls = 0

        async def complete(self, *, system: str, user: str) -> str:
            self.calls += 1
            raise AssertionError("must not be called")

    llm = _Boom()
    payload = {
        "scoring_items": [],
        "qualification_requirements": [],
        "reject_clauses": [],
    }
    report = await apply_fallback(
        payload, _parsed("评分：技术 40 分"), uie_enabled=False, llm_enabled=False, llm_client=llm
    )
    assert report.uie_used is False and report.llm_used is False
    assert llm.calls == 0


class _FakeLlm:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        assert system == LLM_SYSTEM_PROMPT
        assert "[0|页1]" in user
        return self.content


async def test_llm_extract_requires_evidence_and_maps_page() -> None:
    parsed = _parsed("技术方案应满足等保三级要求，技术部分 40 分")
    candidates = candidate_blocks(parsed, ["scoring"])
    llm = _FakeLlm(
        json.dumps(
            {
                "scoring_items": [
                    {"item": "技术部分", "score": 40, "evidence": "技术部分 40 分"},
                    {"item": "无证据项", "score": 10, "evidence": ""},
                ],
                "qualifications": [],
                "reject_clauses": [],
            },
            ensure_ascii=False,
        )
    )

    extracted = await llm_extract(llm, candidates, ["scoring"])

    assert extracted is not None
    assert len(extracted["scoring"]) == 1  # 无证据项被丢弃
    assert extracted["scoring"][0]["page"] == 1
    assert extracted["scoring"][0]["score"] == 40.0


async def test_llm_extract_invalid_json_returns_none() -> None:
    parsed = _parsed("评分：技术 40 分")
    candidates = candidate_blocks(parsed, ["scoring"])
    llm = _FakeLlm("不是 JSON")

    assert await llm_extract(llm, candidates, ["scoring"]) is None

    fenced = _FakeLlm("```json\n[]\n```")  # 合法 JSON 但非对象 → 按解析失败处理
    assert await llm_extract(fenced, candidates, ["scoring"]) is None


async def test_apply_fallback_full_flow_merge_and_meta() -> None:
    payload = {
        "scoring_items": [],
        "qualification_requirements": [],
        "reject_clauses": [],
    }
    parsed = _parsed(
        "评标办法：技术响应能力 30 分",
        "投标人须具备信息安全服务资质",
        "未按要求密封的投标将被否决",
    )
    taskflow = _FakeTaskflow(
        [
            {
                "评分项": [{"text": "技术响应能力", "probability": 0.9}],
                "资格要求": [{"text": "须具备信息安全服务资质", "probability": 0.88}],
                "废标条款": [],
            },
            {
                "评分项": [],
                "资格要求": [],
                "废标条款": [{"text": "未按要求密封的投标将被否决", "probability": 0.95}],
            },
            {
                "评分项": [],
                "资格要求": [],
                "废标条款": [],
            },
        ]
    )
    extractor = UieExtractor(taskflow_factory=lambda: taskflow)
    llm = _FakeLlm(
        json.dumps({"scoring_items": [], "qualifications": [], "reject_clauses": []})
    )

    report = await apply_fallback(
        payload,
        parsed,
        uie_enabled=True,
        llm_enabled=True,
        llm_client=llm,
        uie_extractor=extractor,
    )

    assert report.uie_used is True and report.uie_items == 3
    assert report.llm_used is True and report.llm_items == 0
    assert payload["scoring_items"][0]["confidence"] == "uie"
    assert payload["scoring_items"][0]["source_span"]["quote"] == "技术响应能力"
    assert payload["qualification_requirements"][0]["name"] == "须具备信息安全服务资质"
    assert payload["reject_clauses"][0]["severity"] == "HARD"
    # 规则层键形状逐键镜像
    assert set(payload["scoring_items"][0]) == {
        "category", "item", "score", "criteria", "source_span", "confidence",
    }
    assert set(payload["qualification_requirements"][0]) == {
        "category", "text", "name", "level", "quantity", "source_span", "confidence",
    }


async def test_apply_fallback_llm_failure_records_error_not_raise() -> None:
    class _Broken:
        async def complete(self, *, system: str, user: str) -> str:
            raise RuntimeError("gateway down")

    payload = {"scoring_items": [], "qualification_requirements": [], "reject_clauses": []}
    report = await apply_fallback(
        payload,
        _parsed("评分：技术 40 分"),
        uie_enabled=False,
        llm_enabled=True,
        llm_client=_Broken(),
    )
    assert report.llm_used is False
    assert report.llm_error and report.llm_error.startswith("llm_fallback_failed")
