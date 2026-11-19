"""跨投标人「语义级」相似度自查（纯确定性、零第三方依赖、不调用 LLM）。

**能力边界（竞品调研文档 §6.3 明令）**：本模块只做合规自查与风险提示，
不输出「串标」定性结论，也不提供任何规避检测建议；结果必须由人工复核。

为什么需要它：现有 `similarity.compare` 只做字符 4-gram Jaccard + 最长公共片段，
对「同义改写」几乎不敏感（换一组近义词就能把字面分压到接近 0）。这里在不引入
分词器 / 模型 / 外部语料的前提下，补一层语义级近似。

## 算法（三层确定性信号，全部可解释）

1. **概念归一化**：内置概念词典把同义词 / 近义词折叠为同一个概念
   （如「技术方案 / 技术建议书 / 技术标」→ 同一概念），使改写后的表述在
   概念维度上对齐；匹配按「长词优先」单遍扫描，避免短词吃掉长词。
2. **概念层 `concept`**：概念计数向量的余弦相似度（0–1）。这是唯一对
   「同义改写」敏感的一层，因此权重最高。
3. **字符 n-gram 层 `ngram`**：归一化原文上字符 2–3 gram 集合的 Jaccard；
   含虚词 / 高频功能字的 gram 按 `FUNCTION_CHARS` 过滤，纯虚词 gram 直接丢弃，
   避免「的、了、和」这类填充把无关文本抬分。它反映措辞与结构相似度。
4. **术语层 `term`**：内置专业术语词表的 Jaccard（0–1），反映「是不是在同一套
   专业语汇里说话」。它对同义改写不敏感，正好与概念层互补：概念层高而术语层低，
   往往说明是「换了说法讲同一件事」，这正是需要人工复核的信号。

`semantic_score = CONCEPT_WEIGHT×concept + NGRAM_WEIGHT×ngram + TERM_WEIGHT×term`，
权重见下方常量注释（三者之和为 1，故分值必落在 0–1）。

## 为什么确定性

无随机数、无时间、无模型、无网络、无外部语料；同一输入在任意进程、任意机器上
得到同一结果。具体保障：
- 所有集合在参与计算与输出前显式 `sorted()`——Python 的 str 哈希随进程随机化，
  直接迭代 set 会破坏可复现性；n-gram 用 `zlib.crc32`（固定 CRC 表，跨进程稳定）
  折叠为定长整数后再按数值排序，完全不依赖 `hash()`；
- n-gram 超过 `NGRAM_MAX_UNIQUE` 时按 CRC32 **最小的 k 个**抽样：同一文本必然
  抽出同一批 gram，抽样规则本身也是纯函数（这是确定性的 bottom-k 估计，
  不是随机采样）；
- 浮点求和使用 `math.fsum`（精确求和，与累加顺序无关）；
- 词典是模块级不可变常量，概念词按 `(-长度, 字面)` 排序后编译正则，分支顺序固定。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import fsum
from typing import Any
from zlib import crc32

from app.core.errors import AppError
from app.modules.file_check.similarity import normalize

# 单份文件参与语义比较的正文字符上限：约等于 150–200 页技术标正文的量级。
# 超限直接报 400，**不做静默截断**——截断后给出的高分/低分都会误导人工复核。
MAX_COMPARE_CHARS = 200_000
# 单次请求所有文件的正文字符总预算：10 份文件两两比较 = 45 对，
# 单份 20 万字的极端输入下 45 对约 4 秒（本机实测），超出预算直接 400，
# 而不是逐份悄悄跳过或降级成空结果。
MAX_TOTAL_COMPARE_CHARS = 600_000

# 正文短于该长度时语义信号不可靠（几个字就能凑出高重合），直接给 0 并在证据里说明。
MIN_SEMANTIC_CHARS = 8

# 语义分等级阈值：与 similarity.py 的 high/medium 阈值保持一致，
# 避免同屏出现两套「高/中/低」口径。
HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.5

# 三层权重之和为 1，保证 semantic_score 落在 0–1。
# 依据：概念归一化后的重合最能反映「换个说法讲同一件事」，是本层存在的唯一理由，
# 权重最高；字符 n-gram 反映措辞与结构，但换同义词就掉分，权重次之；术语层对
# 同义改写不敏感、主要用于区分「专业语汇一致」与「只是通用字面接近」，权重最低。
CONCEPT_WEIGHT = 0.6
NGRAM_WEIGHT = 0.25
TERM_WEIGHT = 0.15

# 综合排序权重：字面 0.5 + 语义 0.5。
# 依据：两个分数量纲一致（0–1）；字面雷同可直接引用原文、证据更强，语义近似
# 需要人工复核，等权避免任一信号被淹没。组合仍落在 0–1，排序方向与旧版 score
# 一致（越大越需要复核）。
COMBINED_LEXICAL_WEIGHT = 0.5
COMBINED_SEMANTIC_WEIGHT = 0.5

NGRAM_SIZES = (2, 3)
# 每份文件保留的 n-gram 抽样上限（按 CRC32 最小的 k 个，确定性）。见 _gram_hashes。
NGRAM_MAX_UNIQUE = 20_000

# 虚词 / 功能字：只降权、不删除（「由谁在何时做什么」仍可能靠它们区分）。
FUNCTION_CHARS = frozenset(
    "的了和与及或在是为对由按其本该等中上下不有无应须可能以之也并且但而就都还很更最"
    "这那有为于一所被把从到同跟向再又才只仅已将对给让们你要会需着过吧呢吗啊哦"
)
# gram 的「有效信息占比」门槛：非虚词字数 / gram 长度 低于 1/2 就丢弃，
# 整段虚词的 gram 不携带主题信息。用整数比较实现（见 _gram_hashes），不引入浮点。

# 共同片段证据：只展示字面层面真实存在的连续片段，供人工回原文核对；
# 语义层命中的概念另由 concept_pairs 说明，二者不混淆。
EVIDENCE_EXCERPT_MIN = 12
EVIDENCE_EXCERPT_LIMIT = 3
EVIDENCE_EXCERPT_MAX = 120
# 证据片段搜索窗口上限：SequenceMatcher 最坏 O(n×m)，窗口过大会拖慢两两比较。
MAX_EVIDENCE_CHARS = 4_000

BOUNDARY = "仅用于跨投标人合规自查与人工复核，不构成「串标」认定，不提供任何规避检测建议。"

FORMULA = (
    f"semantic_score = {CONCEPT_WEIGHT:.2f} × concept"
    f" + {NGRAM_WEIGHT:.2f} × ngram"
    f" + {TERM_WEIGHT:.2f} × term（各层均为 0–1）"
)


class FileCheckLimitError(AppError):
    """明确的 400：超出比较上限必须报错，不允许静默截断或降级为空结果。"""

    status_code = 400
    code = "file_check_limit_exceeded"


# 概念词典：同一概念下是「同义 / 近义」的确定性词表（人工维护，不依赖外部语料）。
# 只收投标文件里的领域概念；「方案 / 计划 / 项目 / 服务」这类过于泛化的词不收，
# 否则会把无关文本拉到同一个概念上，语义分失真。
CONCEPT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("技术方案", ("技术方案", "技术建议书", "技术标", "技术标书", "技术响应", "技术文件", "技术部分")),
    (
        "实施方案",
        ("实施方案", "实施计划", "实施安排", "项目实施方案", "实施组织", "实施步骤", "实施方法"),
    ),
    ("运维服务", ("运维服务", "运行维护", "运营维护", "运维支持", "维护服务", "日常维护", "运维保障")),
    ("售后服务", ("售后服务", "售后支持", "售后保障", "质保服务", "保修服务", "售后维护")),
    ("项目管理", ("项目管理", "项目管理体系", "项目管理制度", "项目管理办法", "项目管控")),
    ("项目团队", ("项目团队", "项目组", "项目班子", "团队配置", "人员配置", "人员安排", "团队组成")),
    ("质量管理", ("质量管理", "质量保证", "质量控制", "质量保障", "质量管理体系", "质量目标")),
    ("进度计划", ("进度计划", "工期计划", "时间计划", "项目进度", "进度安排", "工期安排", "进度控制")),
    ("培训", ("培训", "技术培训", "操作培训", "使用培训", "培训计划", "培训方案", "培训安排")),
    ("验收", ("验收", "竣工验收", "项目验收", "交付验收", "验收标准", "验收方案", "验收流程")),
    ("系统架构", ("系统架构", "总体架构", "技术架构", "架构设计", "总体设计", "系统架构设计")),
    ("系统集成", ("系统集成", "集成方案", "集成实施", "系统对接", "集成开发")),
    ("软件开发", ("软件开发", "软件研发", "应用开发", "定制开发", "软件设计")),
    ("信息安全", ("信息安全", "网络安全", "信息安全管理", "安全防护", "数据安全", "安全体系")),
    ("应急", ("应急预案", "应急处置", "应急响应", "应急保障", "应急措施", "应急演练")),
    ("风险控制", ("风险控制", "风险管理", "风险防控", "风险应对", "风险识别", "风险评估")),
    (
        "交付",
        ("按期交付", "按时交付", "如期交付", "按时完成", "按期完成", "准时交付", "交付周期", "履约交付"),
    ),
    ("服务响应", ("服务响应", "响应时间", "响应机制", "故障响应", "服务承诺", "响应速度")),
    ("故障处理", ("故障处理", "故障排除", "故障修复", "问题处理", "故障定位")),
    ("备品备件", ("备品备件", "备件供应", "备件库", "备件管理")),
    ("巡检", ("巡检", "例行巡检", "日常巡检", "定期巡检", "巡检报告", "巡查")),
    ("数据中心", ("数据中心", "机房", "机房建设", "数据机房", "机房环境")),
    ("沟通协调", ("沟通机制", "沟通协调", "协调机制", "沟通方案", "协同机制")),
    ("保密", ("保密", "保密措施", "保密管理", "保密协议", "保密承诺", "保密要求")),
    ("廉洁", ("廉洁", "廉洁自律", "廉政", "廉洁承诺", "廉洁从业")),
    ("环境保护", ("环保", "环境保护", "绿色施工", "环境管理", "环境保护措施")),
    ("安全生产", ("安全施工", "安全生产", "施工安全", "安全管理", "安全措施", "安全文明施工")),
    ("数据分析", ("数据分析", "数据挖掘", "数据治理", "数据统计", "数据可视化")),
    ("技术创新", ("技术创新", "技术先进性", "技术创新点", "技术优势", "技术亮点")),
    ("本地化服务", ("本地化服务", "本地服务", "本地化支持", "驻场服务", "驻场", "现场服务")),
    ("合同管理", ("合同管理", "合同履约", "履约管理", "合同执行")),
    ("成本控制", ("成本控制", "成本管理", "费用控制", "投资控制")),
    ("变更管理", ("变更管理", "变更控制", "变更流程", "变更审批")),
    ("上线部署", ("上线", "系统上线", "上线部署", "投产", "部署上线")),
    ("测试", ("测试", "系统测试", "测试方案", "联调测试", "测试计划", "测试报告")),
)

# 术语层词表：只放「专业语汇」的字面形式，不放同义词。
# 同义改写会在这里掉分，抄写/套模板会在这里得分，正好与概念层互补。
TERMS: tuple[str, ...] = tuple(
    sorted(
        {
            "技术方案",
            "技术建议书",
            "实施方案",
            "实施计划",
            "运行维护",
            "运维服务",
            "售后服务",
            "项目管理",
            "质量管理",
            "质量保证",
            "质量控制",
            "进度计划",
            "人员配置",
            "项目团队",
            "培训",
            "验收",
            "系统架构",
            "系统集成",
            "软件开发",
            "信息安全",
            "网络安全",
            "应急预案",
            "风险管理",
            "服务响应",
            "响应时间",
            "故障处理",
            "备品备件",
            "巡检",
            "数据中心",
            "保密措施",
            "廉洁自律",
            "环境保护",
            "安全生产",
            "成本控制",
            "合同管理",
            "测试",
            "上线",
            "交付",
            "技术支持",
            "服务承诺",
            "技术文档",
            "数据迁移",
            "业务连续性",
            "项目管理体系",
            "质量管理体系",
            "安全管理",
            "本地化服务",
            "驻场服务",
            "满意度",
            "服务级别",
            "考核",
            "审计",
            "监理",
            "验收报告",
            "测试报告",
            "维护保养",
            "备件",
            "技术培训",
            "操作培训",
            "需求分析",
            "概要设计",
            "详细设计",
            "部署",
            "回退",
            "备份",
            "恢复",
            "监控",
            "告警",
            "日志",
        }
    )
)


def _build_concept_index() -> tuple[dict[str, str], re.Pattern[str]]:
    term_code: dict[str, str] = {}
    for code, terms in CONCEPT_GROUPS:
        for term in terms:
            normalized = normalize(term)
            if normalized:
                # 同一术语出现在多个概念组时，以先声明者为准（声明顺序固定 → 结果确定）
                term_code.setdefault(normalized, code)
    # 长词优先：保证「项目实施方案」不会被「实施方案」先切走一部分。
    # 同长度按字面排序，保证正则分支顺序与集合迭代顺序无关。
    ordered_terms = tuple(sorted(term_code, key=lambda term: (-len(term), term)))
    pattern = re.compile("|".join(re.escape(term) for term in ordered_terms))
    return term_code, pattern


_TERM_CODE, _CONCEPT_PATTERN = _build_concept_index()


@dataclass(frozen=True)
class DocumentProfile:
    """单份文件的语义画像：只依赖入参文本，可复用于两两比较。"""

    chars: int
    concepts: tuple[tuple[str, int], ...]
    concept_forms: tuple[tuple[str, tuple[str, ...]], ...]
    grams: frozenset[int]
    terms: tuple[str, ...]
    preview: str
    short: bool


def readable_text(text: str) -> str:
    """证据片段用的可读文本：保留标点与词序，折叠空白，统一小写后截断。"""
    value = unicodedata.normalize("NFKC", text).lower()
    value = re.sub(r"\s+", " ", value).strip()
    return value[:MAX_EVIDENCE_CHARS]


def _gram_hashes(canonical: str) -> frozenset[int]:
    """把 2–3 字 gram 折叠为定长整数集合，并做确定性 bottom-k 抽样。

    为什么用 CRC32 而不是内置 hash()：后者对 str 随进程随机化，会破坏「同输入同结果」。

    为什么抽样：单份 20 万字能产生约 40 万个不同 gram，10 份文件两两比较（45 对）
    如果各自持有全集，内存与耗时都随文本量线性膨胀。保留 CRC32 最小的
    `NGRAM_MAX_UNIQUE` 个 gram 是确定性的 bottom-k 抽样——同一文本必然保留同一批
    gram，抽样规则本身是纯函数。**注意**：抽样只在超长文本触发（短文本 gram 数本就不足 k），
    触发时 n-gram 层是近似值，但仍然是同一输入的同一确定值；概念层与术语层不受影响。
    """
    hashes: list[int] = []
    append = hashes.append
    total = len(canonical)
    for size in NGRAM_SIZES:
        for index in range(total - size + 1):
            gram = canonical[index : index + size]
            function_count = 0
            for char in gram:
                if char in FUNCTION_CHARS:
                    function_count += 1
            # 有效信息占比 = (size - 虚字数) / size，低于 GRAM_MIN_INFORMATION 丢弃。
            # 用整数比较避免浮点，判断条件与 GRAM_MIN_INFORMATION=0.5 等价。
            if (size - function_count) * 2 < size:
                continue  # 虚词占比过高：不携带主题信息，直接丢弃
            append(crc32(gram.encode("utf-8")))
    unique = sorted(set(hashes))
    return frozenset(unique[:NGRAM_MAX_UNIQUE])


def prepare_document(text: str) -> DocumentProfile:
    """构建语义画像；超长文本抛 400，绝不静默截断。"""
    if len(text) > MAX_COMPARE_CHARS:
        raise FileCheckLimitError(
            f"单份文件正文超过 {MAX_COMPARE_CHARS} 字上限（当前 {len(text)} 字），"
            "请拆分后再做跨投标人自查；系统不会截断后给结论。"
        )
    canonical = normalize(text)
    concept_counts: Counter[str] = Counter()
    concept_forms: dict[str, set[str]] = {}
    for match in _CONCEPT_PATTERN.finditer(canonical):
        term = match.group(0)
        label = _TERM_CODE[term]
        concept_counts[label] += 1
        concept_forms.setdefault(label, set()).add(term)
    return DocumentProfile(
        chars=len(text),
        concepts=tuple(sorted(concept_counts.items())),
        concept_forms=tuple(
            sorted((label, tuple(sorted(forms))) for label, forms in concept_forms.items())
        ),
        grams=_gram_hashes(canonical),
        terms=tuple(sorted({term for term in TERMS if term in canonical})),
        preview=readable_text(text),
        short=len(canonical) < MIN_SEMANTIC_CHARS,
    )


def _cosine(left: tuple[tuple[str, int], ...], right: tuple[tuple[str, int], ...]) -> float:
    if not left or not right:
        return 0.0
    left_map = dict(left)
    right_map = dict(right)
    # 用较短的一侧做累加，减少查表次数；两侧都已排序，结果与迭代顺序无关。
    if len(left_map) <= len(right_map):
        small, big = left_map, right_map
    else:
        small, big = right_map, left_map
    dot = fsum(count * big.get(key, 0) for key, count in small.items())
    norm_left = fsum(count * count for _, count in left)
    norm_right = fsum(count * count for _, count in right)
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left**0.5 * norm_right**0.5)


def _intersection_size(left: frozenset[int], right: frozenset[int]) -> int:
    """交集大小；用较小集合做遍历，减少查表次数。"""
    if len(left) > len(right):
        left, right = right, left
    return sum(1 for value in left if value in right)


def _jaccard_sets(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[float, int, int]:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0, 0, 0
    shared = len(left_set & right_set)
    return shared / len(union), shared, len(union)


def _jaccard_hashes(left: frozenset[int], right: frozenset[int]) -> tuple[float, int, int]:
    shared = _intersection_size(left, right)
    union_size = len(left) + len(right) - shared
    if not union_size:
        return 0.0, 0, 0
    return shared / union_size, shared, union_size


def _shared_fragments(left: str, right: str) -> tuple[str, ...]:
    """字面层面的共同片段（最长优先，最多 3 段），供人工回原文核对。"""
    if not left or not right:
        return ()
    fragments: list[str] = []
    pending: list[tuple[str, str]] = [(left, right)]
    while pending and len(fragments) < EVIDENCE_EXCERPT_LIMIT:
        first, second = pending.pop(0)
        if not first or not second:
            continue
        match = SequenceMatcher(None, first, second, autojunk=False).find_longest_match(
            0, len(first), 0, len(second)
        )
        if match.size < EVIDENCE_EXCERPT_MIN:
            continue
        fragments.append(first[match.a : match.a + min(match.size, EVIDENCE_EXCERPT_MAX)])
        pending.append((first[: match.a], second[: match.b]))
        pending.append((first[match.a + match.size :], second[match.b + match.size :]))
    return tuple(fragments)


def semantic_level(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _evidence(
    left: DocumentProfile,
    right: DocumentProfile,
    *,
    concept: float = 0.0,
    ngram: float = 0.0,
    term: float = 0.0,
    score: float = 0.0,
    counts: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """依据链：命中术语、共同概念及两侧原词、共同片段、各层贡献与合成公式。"""
    right_terms = set(right.terms)
    right_concepts = dict(right.concepts)
    right_forms = dict(right.concept_forms)
    left_forms = dict(left.concept_forms)
    shared_terms = [term_name for term_name in left.terms if term_name in right_terms][:20]
    shared_concepts = [label for label, _ in left.concepts if label in right_concepts]
    concept_pairs = [
        {
            "concept": label,
            # 概念相同但字面可能不同——这正是「语义级」的可解释证据所在
            "left": list(left_forms.get(label, ())),
            "right": list(right_forms.get(label, ())),
        }
        for label in shared_concepts
    ]
    fragments = _shared_fragments(left.preview, right.preview) if score >= MEDIUM_THRESHOLD else ()
    evidence: dict[str, Any] = {
        "shared_terms": shared_terms,
        "shared_concepts": shared_concepts,
        "concept_pairs": concept_pairs,
        "shared_excerpts": list(fragments),
        "contributions": {
            "concept": {
                "raw": round(concept, 4),
                "weight": CONCEPT_WEIGHT,
                "contribution": round(concept * CONCEPT_WEIGHT, 4),
            },
            "ngram": {
                "raw": round(ngram, 4),
                "weight": NGRAM_WEIGHT,
                "contribution": round(ngram * NGRAM_WEIGHT, 4),
            },
            "term": {
                "raw": round(term, 4),
                "weight": TERM_WEIGHT,
                "contribution": round(term * TERM_WEIGHT, 4),
            },
        },
        "layer_counts": counts or {},
        "formula": FORMULA,
        "explanation": (
            f"概念层 {concept:.4f}×{CONCEPT_WEIGHT:.2f}"
            f" + 字符 n-gram 层 {ngram:.4f}×{NGRAM_WEIGHT:.2f}"
            f" + 术语层 {term:.4f}×{TERM_WEIGHT:.2f}"
            f" = {score:.4f}"
        ),
        "boundary": BOUNDARY,
    }
    if note is not None:
        evidence["note"] = note
    return evidence


def compare_prepared(left: DocumentProfile, right: DocumentProfile) -> dict[str, Any]:
    """比较两份已构建的画像，返回 semantic_score / semantic_level / semantic_evidence。"""
    if left.short or right.short:
        note = f"正文过短（少于 {MIN_SEMANTIC_CHARS} 字），不做语义比较，语义分记 0"
        return {
            "semantic_score": 0.0,
            "semantic_level": "low",
            "semantic_evidence": _evidence(left, right, note=note),
        }
    concept = _cosine(left.concepts, right.concepts)
    ngram, ngram_shared, ngram_union = _jaccard_hashes(left.grams, right.grams)
    term, term_shared, term_union = _jaccard_sets(left.terms, right.terms)
    score = round(concept * CONCEPT_WEIGHT + ngram * NGRAM_WEIGHT + term * TERM_WEIGHT, 4)
    return {
        "semantic_score": score,
        "semantic_level": semantic_level(score),
        "semantic_evidence": _evidence(
            left,
            right,
            concept=concept,
            ngram=ngram,
            term=term,
            score=score,
            counts={
                "concept_shared": len(
                    {label for label, _ in left.concepts} & {label for label, _ in right.concepts}
                ),
                "ngram_shared": ngram_shared,
                "ngram_union": ngram_union,
                "term_shared": term_shared,
                "term_union": term_union,
            },
        ),
    }


def compare_semantic(left_text: str, right_text: str) -> dict[str, Any]:
    """便捷入口：直接比较两段原文（测试与外部调用方使用）。"""
    return compare_prepared(prepare_document(left_text), prepare_document(right_text))


def combined_score(lexical_score: float, semantic_score: float) -> float:
    """综合分 = 字面 0.5 + 语义 0.5，见文件头部权重注释。"""
    return round(
        lexical_score * COMBINED_LEXICAL_WEIGHT + semantic_score * COMBINED_SEMANTIC_WEIGHT,
        4,
    )
