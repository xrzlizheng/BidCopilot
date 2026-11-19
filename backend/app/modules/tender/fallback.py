"""规则抽取之后的兜底层：本地 PP-UIE 优先、云端 LLM 次之，均默认关闭。

原则（与全项目一致）：
- 规则结果是权威；兜底层只补"空缺/低置信"维度，产出以 confidence="uie"/"llm"
  独立标记——它们是待确认判断，不是当前事实。
- LLM 输出必须带证据摘录（evidence），落到 source_span.quote；schema 失败只记
  warning，规则结果原样保留，绝不编造。
"""

import asyncio
import json
import re
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Protocol

from app.modules.tender.parser import Block, ParsedDocument

MAX_CANDIDATE_BLOCKS = 60
DIMENSIONS = ("scoring", "qualification", "reject")

_SCORING_CUE = re.compile(r"\d+(?:\.\d+)?\s*分|评分|评审|评标")
_QUALIFICATION_CUE = re.compile(r"资质|资格|许可证|营业执照|认证|业绩|财务|审计|信用|持证")
_REJECT_CUE = re.compile(r"废标|否决|无效投标|投标无效|不予受理|拒绝接收|取消投标资格|视为放弃")

_CUES = {
    "scoring": _SCORING_CUE,
    "qualification": _QUALIFICATION_CUE,
    "reject": _REJECT_CUE,
}


@dataclass(frozen=True)
class Deficiency:
    dimension: str  # scoring | qualification | reject
    payload_key: str  # scoring_items | qualification_requirements | reject_clauses


DIMENSION_KEYS = {
    "scoring": "scoring_items",
    "qualification": "qualification_requirements",
    "reject": "reject_clauses",
}


def detect_deficiency(payload: dict[str, Any]) -> list[Deficiency]:
    """空缺或低置信的维度。scoring 非空但全部非 high 视为低置信。"""
    result: list[Deficiency] = []
    scoring = payload.get("scoring_items") or []
    if not scoring or all(item.get("confidence") != "high" for item in scoring):
        result.append(Deficiency("scoring", DIMENSION_KEYS["scoring"]))
    if not payload.get("qualification_requirements"):
        result.append(Deficiency("qualification", DIMENSION_KEYS["qualification"]))
    if not payload.get("reject_clauses"):
        result.append(Deficiency("reject", DIMENSION_KEYS["reject"]))
    return result


def candidate_blocks(
    parsed: ParsedDocument, dimensions: list[str]
) -> list[tuple[int, Block]]:
    """按维度线索词挑候选块（段落优先，封顶 MAX_CANDIDATE_BLOCKS），返回 (行号, 块)。"""
    picked: list[tuple[int, Block]] = []
    seen: set[int] = set()
    paragraphs = [
        (i, block)
        for i, block in enumerate(parsed.blocks)
        if block.kind in ("paragraph", "heading")
    ]
    table_cells = [
        (i, block) for i, block in enumerate(parsed.blocks) if block.kind == "table_cell"
    ]
    for source in (paragraphs, table_cells):
        for index, block in source:
            if len(picked) >= MAX_CANDIDATE_BLOCKS:
                break
            if index in seen:
                continue
            if any(_CUES[dim].search(block.text) for dim in dimensions):
                picked.append((index, block))
                seen.add(index)
    return picked


def uie_available() -> bool:
    """paddlenlp 是否安装；find_spec 不触发重型导入。"""
    return find_spec("paddlenlp") is not None


class UieExtractor:
    """PP-UIE（PaddleNLP Taskflow）零样本 schema 抽取；CPU 推理放线程池。"""

    def __init__(
        self,
        *,
        schema: list[str] | None = None,
        taskflow_factory: Any | None = None,
    ) -> None:
        self.schema = schema or ["评分项", "资格要求", "废标条款"]
        self._taskflow_factory = taskflow_factory
        self._taskflow: Any | None = None

    def _get_taskflow(self) -> Any:
        if self._taskflow is None:
            if self._taskflow_factory is not None:
                self._taskflow = self._taskflow_factory()
            else:
                if not uie_available():
                    raise RuntimeError("paddlenlp is not installed")
                # paddlenlp 是可选依赖（仅 UIE 兜底开启时安装），缺包时 import 失败由上方守卫拦截
                from paddlenlp import Taskflow  # type: ignore[import-not-found]

                self._taskflow = Taskflow("information_extraction", schema=self.schema)
        return self._taskflow

    async def extract(
        self, candidates: list[tuple[int, Block]]
    ) -> dict[str, list[dict[str, Any]]]:
        texts = [block.text for _, block in candidates]
        taskflow = self._get_taskflow()

        def _run() -> list[dict[str, Any]]:
            return list(taskflow(texts))

        raw = await asyncio.to_thread(_run)
        return self._collect(raw, candidates)

    def _collect(
        self, raw: list[dict[str, Any]], candidates: list[tuple[int, Block]]
    ) -> dict[str, list[dict[str, Any]]]:
        dimension_by_schema = {
            "评分项": "scoring",
            "资格要求": "qualification",
            "废标条款": "reject",
        }
        collected: dict[str, list[dict[str, Any]]] = {
            "scoring": [],
            "qualification": [],
            "reject": [],
        }
        for entry, (index, block) in zip(raw, candidates, strict=False):
            if not isinstance(entry, dict):
                continue
            for schema_name, dimension in dimension_by_schema.items():
                spans = entry.get(schema_name) or []
                if not isinstance(spans, list):
                    continue
                for span in spans:
                    text = span.get("text") if isinstance(span, dict) else None
                    if not isinstance(text, str) or not text.strip():
                        continue
                    probability = (
                        span.get("probability") if isinstance(span, dict) else None
                    )
                    collected[dimension].append(
                        {
                            "text": text.strip()[:300],
                            "block_index": index,
                            "page": block.page,
                            "probability": probability,
                        }
                    )
        return collected


LLM_SYSTEM_PROMPT = (
    "你是招标文件结构化抽取器。只依据给出的候选行抽取，不要编造；"
    "每条结果必须带 evidence（候选行中的原文片段）。"
    '只输出 JSON 对象：{"scoring_items":[{"item":"...","score":数字或null,"evidence":"..."}],'
    '"qualifications":[{"requirement":"...","evidence":"..."}],'
    '"reject_clauses":[{"text":"...","evidence":"..."}]}，没有就给空数组。'
)


class LlmClientLike(Protocol):
    async def complete(self, *, system: str, user: str) -> str: ...


def _numbered_lines(candidates: list[tuple[int, Block]]) -> str:
    return "\n".join(
        f"[{index}|页{block.page or '?'}] {block.text[:200]}" for index, block in candidates
    )


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _page_for_evidence(
    evidence: str, candidates: list[tuple[int, Block]]
) -> tuple[int | None, int | None]:
    fragment = re.sub(r"\s+", "", evidence or "")[:24]
    if not fragment:
        return None, None
    for index, block in candidates:
        if fragment in re.sub(r"\s+", "", block.text):
            return block.page, index
    return None, None


async def llm_extract(
    client: LlmClientLike,
    candidates: list[tuple[int, Block]],
    dimensions: list[str],
) -> dict[str, Any] | None:
    """返回 {dimension: [item,...]}；解析/校验失败返回 None（由调用方记 warning）。"""
    wanted = "、".join(dimensions)
    user = (
        f"需要补抽的维度：{wanted}。\n候选行（[行号|页码] 文本）：\n{_numbered_lines(candidates)}"
    )
    content = await client.complete(system=LLM_SYSTEM_PROMPT, user=user)
    data = _parse_llm_json(content)
    if data is None:
        return None
    mapping = {
        "scoring": data.get("scoring_items"),
        "qualification": data.get("qualifications"),
        "reject": data.get("reject_clauses"),
    }
    result: dict[str, Any] = {}
    for dimension in dimensions:
        entries = mapping.get(dimension)
        if not isinstance(entries, list):
            result[dimension] = []
            continue
        cleaned: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            evidence = str(entry.get("evidence") or "").strip()
            text = str(entry.get("item") or entry.get("requirement") or entry.get("text") or "").strip()
            if not text or not evidence:
                continue  # 无证据的结果不可信，直接丢弃
            page, block_index = _page_for_evidence(evidence, candidates)
            score = entry.get("score")
            cleaned.append(
                {
                    "text": text[:300],
                    "score": float(score) if isinstance(score, (int, float)) else None,
                    "evidence": evidence[:240],
                    "page": page,
                    "block_index": block_index,
                }
            )
        result[dimension] = cleaned
    return result


def _merge_uie_items(payload: dict[str, Any], collected: dict[str, Any]) -> int:
    """UIE 结果并入 payload，逐键镜像规则层形状；返回并入条数。"""
    merged = 0
    existing_scoring = {
        re.sub(r"\s+", "", item.get("item", "")) for item in payload.get("scoring_items") or []
    }
    for item in collected.get("scoring") or []:
        key = re.sub(r"\s+", "", item["text"])
        if not key or key in existing_scoring:
            continue
        existing_scoring.add(key)
        payload["scoring_items"].append(
            {
                "category": "其他",
                "item": item["text"],
                "score": None,
                "criteria": None,
                "source_span": {
                    "block_index": item["block_index"],
                    "page": item["page"],
                    "quote": item["text"],
                },
                "confidence": "uie",
            }
        )
        merged += 1
    existing_qual = {
        re.sub(r"\s+", "", item.get("text", ""))[:80]
        for item in payload.get("qualification_requirements") or []
    }
    for item in collected.get("qualification") or []:
        key = re.sub(r"\s+", "", item["text"])[:80]
        if not key or key in existing_qual:
            continue
        existing_qual.add(key)
        payload["qualification_requirements"].append(
            {
                "category": "OTHER",
                "text": item["text"],
                "name": item["text"][:200],
                "level": None,
                "quantity": None,
                "source_span": {
                    "block_index": item["block_index"],
                    "page": item["page"],
                    "quote": item["text"],
                },
                "confidence": "uie",
            }
        )
        merged += 1
    existing_reject = {
        re.sub(r"\s+", "", item.get("text", ""))[:80]
        for item in payload.get("reject_clauses") or []
    }
    for item in collected.get("reject") or []:
        key = re.sub(r"\s+", "", item["text"])[:80]
        if not key or key in existing_reject:
            continue
        existing_reject.add(key)
        payload["reject_clauses"].append(
            {
                "text": item["text"],
                "source_span": {
                    "block_index": item["block_index"],
                    "page": item["page"],
                    "quote": item["text"],
                },
                "severity": "HARD",
                "confidence": "uie",
            }
        )
        merged += 1
    return merged


def _merge_llm_items(payload: dict[str, Any], extracted: dict[str, Any]) -> int:
    merged = 0
    existing_scoring = {
        re.sub(r"\s+", "", item.get("item", "")) for item in payload.get("scoring_items") or []
    }
    for item in extracted.get("scoring") or []:
        key = re.sub(r"\s+", "", item["text"])
        if not key or key in existing_scoring:
            continue
        existing_scoring.add(key)
        payload["scoring_items"].append(
            {
                "category": "其他",
                "item": item["text"],
                "score": item.get("score"),
                "criteria": item.get("evidence"),
                "source_span": {
                    "block_index": item.get("block_index"),
                    "page": item.get("page"),
                    "quote": item.get("evidence"),
                },
                "confidence": "llm",
            }
        )
        merged += 1
    existing_qual = {
        re.sub(r"\s+", "", item.get("text", ""))[:80]
        for item in payload.get("qualification_requirements") or []
    }
    for item in extracted.get("qualification") or []:
        key = re.sub(r"\s+", "", item["text"])[:80]
        if not key or key in existing_qual:
            continue
        existing_qual.add(key)
        payload["qualification_requirements"].append(
            {
                "category": "OTHER",
                "text": item["text"],
                "name": item["text"][:200],
                "level": None,
                "quantity": None,
                "source_span": {
                    "block_index": item.get("block_index"),
                    "page": item.get("page"),
                    "quote": item.get("evidence"),
                },
                "confidence": "llm",
            }
        )
        merged += 1
    existing_reject = {
        re.sub(r"\s+", "", item.get("text", ""))[:80]
        for item in payload.get("reject_clauses") or []
    }
    for item in extracted.get("reject") or []:
        key = re.sub(r"\s+", "", item["text"])[:80]
        if not key or key in existing_reject:
            continue
        existing_reject.add(key)
        payload["reject_clauses"].append(
            {
                "text": item["text"],
                "source_span": {
                    "block_index": item.get("block_index"),
                    "page": item.get("page"),
                    "quote": item.get("evidence"),
                },
                "severity": "HARD",
                "confidence": "llm",
            }
        )
        merged += 1
    return merged


@dataclass
class FallbackReport:
    uie_used: bool = False
    llm_used: bool = False
    dimensions: list[str] = None  # type: ignore[assignment]
    uie_items: int = 0
    llm_items: int = 0
    llm_error: str | None = None
    uie_error: str | None = None


async def apply_fallback(
    payload: dict[str, Any],
    parsed: ParsedDocument,
    *,
    uie_enabled: bool,
    llm_enabled: bool,
    llm_client: LlmClientLike | None = None,
    uie_extractor: UieExtractor | None = None,
) -> FallbackReport:
    """按 规则→UIE→LLM 次序补缺；不抛错（失败记 report 供 warning）。"""
    report = FallbackReport(dimensions=[])
    deficiency = detect_deficiency(payload)
    if not deficiency:
        return report
    dimensions = [item.dimension for item in deficiency]
    report.dimensions = dimensions

    candidates = candidate_blocks(parsed, dimensions)
    if not candidates:
        return report

    if uie_enabled:
        extractor = uie_extractor or UieExtractor()
        try:
            collected = await extractor.extract(candidates)
            report.uie_used = True
            report.uie_items = _merge_uie_items(payload, collected)
        except Exception as exc:
            report.uie_error = f"{type(exc).__name__}: {exc}"

    still_deficient = detect_deficiency(payload)
    llm_dimensions = [item.dimension for item in still_deficient]
    if llm_enabled and llm_dimensions and llm_client is not None:
        try:
            extracted = await llm_extract(llm_client, candidates, llm_dimensions)
            if extracted is None:
                report.llm_error = "llm_fallback_failed: invalid JSON or schema"
            else:
                report.llm_used = True
                report.llm_items = _merge_llm_items(payload, extracted)
        except Exception as exc:
            report.llm_error = f"llm_fallback_failed: {type(exc).__name__}: {exc}"
    return report
