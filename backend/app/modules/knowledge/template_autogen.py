"""历史标书 → 可复用结构模板草稿（知识库冷启动 P0-4）。

设计约束（与 AGENTS.md 一致）：

- **纯确定性**：只做 heading 归并、关键词频次、材料线索抽取，不调用 LLM，
  也不做任何猜测性填充。同样的输入必然产出同样的章节骨架。
- **不造假**：章节标题一定来自已入库文档的真实 heading；`required_materials`
  一定来自正文「须/应提供…」句式或固定材料词表的命中；任何一项证据不足就
  直接报错，不生成空模板。
- **草稿态**：产出 `status="draft"` 的模板，必须由具备 `template:publish` 的人
  确认后发布，才能被项目应用（`apply_template` 拒绝非 published 模板）。
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import can_access_kb, filter_documents
from app.modules.knowledge.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgePack,
)

MIN_SOURCE_DOCUMENTS = 2
MIN_SECTIONS = 3
MAX_SECTIONS = 12
MAX_TITLE_CHARS = 40
MIN_TITLE_CHARS = 2
MAX_MATERIALS_PER_SECTION = 4
HISTORY_TAG = "历史标书"

# 只用于「词表命中」这一个确定性判断，不做语义推断。
MATERIAL_VOCAB: tuple[str, ...] = (
    "营业执照",
    "资质证书",
    "信息系统集成资质",
    "安全生产许可证",
    "项目经理证书",
    "人员配置表",
    "业绩合同",
    "类似项目业绩",
    "报价表",
    "商务偏离表",
    "技术方案",
    "实施计划",
    "培训方案",
    "服务承诺",
    "检测报告",
    "产品授权书",
    "投标函",
    "法定代表人授权书",
    "投标保证金",
    "财务报表",
    "信用证明",
    "验收报告",
)

# 这些 heading 只是文档结构噪声，不能当章节模板。
GENERIC_HEADINGS = frozenset(
    {"目录", "封面", "附件", "说明", "声明", "前言", "索引", "正文", "附表", "附图"}
)

SECTION_TYPE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("投标函", "封面", "致函"), "cover"),
    (("资格", "资质", "证书", "许可"), "qualification"),
    (("业绩", "案例", "业绩证明"), "commercial"),
    (("报价", "商务", "价格", "合同条款"), "commercial"),
    (("售后", "运维", "服务承诺", "培训", "质保"), "service"),
    (("技术", "方案", "实施", "进度", "质量", "安全"), "technical"),
    (("承诺",), "commitment"),
)

NUMBERING_PATTERN = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零〇\d]+[章节部分篇]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十\d]+[）)]"
    r"|\d+(?:[.．]\d+)*[、.．]"
    r"|\d+(?:[.．]\d+)*(?=\s)"
    r"|[#＃]+"
    r")\s*"
)
TRAILING_NOTE_PATTERN = re.compile(r"[（(][^（()）]{0,20}[）)]\s*$")
MATERIAL_CUE_PATTERN = re.compile(
    r"(?:须|需|应|必须|应当)\s*(?:提供|提交|附上|附|出具)([^。；;\n]{2,60})"
)
# 只按明确的列举分隔符切分。中文连词（与/及/和）不做分隔符——
# 「社会保险」会被劈成「社会保」+「险」，比不切更糟。
MATERIAL_SPLIT_PATTERN = re.compile(r"[、，,；;/]|以及")
VOCAB_BY_LENGTH = tuple(sorted(MATERIAL_VOCAB, key=len, reverse=True))
# 要点词表：只用来把中文二元切词的产物收敛成可读的领域词。
# kb._keywords 产出的是二元组（「投标」「标函」「交投」），直接塞进模板
# 会得到一串碎片；这里用固定词表把命中项还原成整词，**不引入任何推断**：
# 没有命中词表就宁可不写「要点」，也不写碎片。
KEYWORD_VOCAB: tuple[str, ...] = (
    "双活",
    "高可用",
    "架构",
    "运维",
    "巡检",
    "实施",
    "实施计划",
    "进度",
    "质量",
    "安全",
    "风险",
    "培训",
    "服务",
    "响应",
    "质保",
    "验收",
    "报价",
    "商务",
    "技术",
    "方案",
    "资质",
    "业绩",
    "案例",
    "人员",
    "证书",
    "授权",
    "偏离",
    "承诺",
    "保密",
    "暗标",
    "格式",
    "国产化",
    "集成",
    "迁移",
    "测试",
    "交付",
    "工期",
    "成本",
    "标准",
    "流程",
)
# 逐行识别标题时用的编号前缀。比 kb.HEADING_PATTERN 宽：历史标书里
# 「第1章」「1.2 技术方案」这类阿拉伯数字编号很常见，kb 的切片器只认中文数字
# 编号，所以短文档常常整篇落在一个无 heading 的 chunk 里。模板生成必须自己
# 从正文行首再识别一次，否则真实文档会全部被判成「无章节证据」。
LINE_NUMBERING_PATTERN = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零〇\d]+[章节部分篇]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十\d]+[）)]"
    r"|\d+(?:[.．]\d+)*[、.．]"
    r"|\d+(?:[.．]\d+)*(?=\s)"
    r"|[#＃]+"
    r")\s*"
)


@dataclass(frozen=True)
class SourceChunk:
    """抽模板所需的最小 chunk 视图（便于单测不依赖 ORM 会话）。"""

    document_id: UUID
    document_title: str
    chunk_index: int
    heading: str | None
    text: str
    keywords: tuple[str, ...] = ()


@dataclass
class _SectionDraft:
    title: str
    documents: set[UUID] = field(default_factory=set)
    chunk_count: int = 0
    order_key: tuple[int, int] = (0, 0)
    keywords: Counter[str] = field(default_factory=Counter)
    keyword_docs: dict[str, set[UUID]] = field(default_factory=dict)
    materials: Counter[str] = field(default_factory=Counter)


def _strip_numbering(text: str) -> str:
    value = text.strip()
    for _ in range(4):
        stripped = NUMBERING_PATTERN.sub("", value).strip()
        if stripped == value:
            break
        value = stripped
    value = TRAILING_NOTE_PATTERN.sub("", value).strip()
    return value.strip(" \t:：、.．-—·　")


def normalize_heading(raw: str | None) -> str:
    """把 heading 归一化成可聚类的章节标题；不可用时返回空串。"""
    if not raw:
        return ""
    title = _strip_numbering(str(raw))
    if len(title) < MIN_TITLE_CHARS or len(title) > MAX_TITLE_CHARS:
        return ""
    if title in GENERIC_HEADINGS:
        return ""
    # heading 里带整段正文的（解析失败产物）不算标题
    if "\n" in title or "。" in title:
        return ""
    return title


def section_type_for(title: str) -> str:
    for tokens, section_type in SECTION_TYPE_RULES:
        if any(token in title for token in tokens):
            return section_type
    return "other"


def _material_cues(text: str) -> Counter[str]:
    """从正文「须/应（提供|提交）…」句式中抽取材料名。

    切分只认明确列举符（、，,；;/以及），随后在每个片段里做词表归一：
    「营业执照与信息系统集成资质」会归成「营业执照」「信息系统集成资质」两项，
    而不是把整句当成一个材料名。
    """
    cues: Counter[str] = Counter()
    for match in MATERIAL_CUE_PATTERN.finditer(text):
        for segment in MATERIAL_SPLIT_PATTERN.split(match.group(1)):
            for name in _normalize_material_segment(segment):
                cues[name] += 1
    return cues


def _normalize_material_segment(segment: str) -> list[str]:
    token = segment.strip(" \t　.。:：与及和")
    if len(token) < MIN_TITLE_CHARS or token.isdigit():
        return []
    hits: list[str] = []
    remainder = token
    for name in VOCAB_BY_LENGTH:
        if name in remainder:
            hits.append(name)
            remainder = remainder.replace(name, "")
    if hits:
        return hits
    if len(token) > 20:
        return []
    return [token]


def _vocab_materials(keywords: Counter[str]) -> Counter[str]:
    """词表命中：只接受能覆盖词条大部的关键词，避免「方案」这类泛词把
    「培训方案」这类具体材料凭空带进模板（那是无证据的材料名）。"""
    hits: Counter[str] = Counter()
    for token, weight in keywords.items():
        if len(token) < 2:
            continue
        for name in MATERIAL_VOCAB:
            if token == name or name in token or token in name and len(token) * 2 >= len(name) + 2:
                hits[name] += weight
    return hits


def _rank_materials(section: _SectionDraft) -> list[str]:
    """材料线索排序：正文句式证据（×3）> 词表命中。"""
    merged: Counter[str] = Counter()
    for name, weight in section.materials.items():
        merged[name] += weight * 3
    for name, weight in _vocab_materials(section.keywords).items():
        merged[name] += weight
    ranked = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ranked[:MAX_MATERIALS_PER_SECTION]]


def _segment_keywords(body: str) -> Counter[str]:
    """把片段正文归一到要点词表；只统计出现在文本里的词表词。

    确定性：同样的正文必然得到同样的计数；不做同义词归并、不做语义推断。
    """
    if not body.strip():
        return Counter()
    counts: Counter[str] = Counter()
    for word in KEYWORD_VOCAB:
        occurrences = body.count(word)
        if occurrences:
            counts[word] = occurrences
    return counts


def _top_keywords(section: _SectionDraft, limit: int = 4) -> list[str]:
    """高频要点按「出现文档数 → 频次 → 词长」排序。

    优先跨文档一致的要点；词长兜底让「实施计划」这类长词排在「实施」之前，
    避免同一概念的长短两个词同时出现。
    """
    ranked = sorted(
        section.keywords.items(),
        key=lambda item: (
            -len(section.keyword_docs.get(item[0], set())),
            -item[1],
            -len(item[0]),
            item[0],
        ),
    )
    result: list[str] = []
    for token, _ in ranked:
        if token in section.title:
            continue
        # 同一概念的长短词只留一个（已按词长降序，先到的是长词）
        if any(token in chosen or chosen in token for chosen in result):
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _has_line_numbering(line: str) -> bool:
    return bool(LINE_NUMBERING_PATTERN.match(line))


def extract_line_headings(text: str) -> list[str]:
    """从一段正文里按行首编号提取标题（chunk 自带 heading 缺失时的兜底）。

    只认「短行 + 行首编号 + 不以句号结尾」的行：这是标书正文里唯一可以
    确定性判定的标题形态，不做任何语义猜测。
    """
    titles: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > MAX_TITLE_CHARS + 12:
            continue
        if not _has_line_numbering(line):
            continue
        if line.endswith(("。", "；", ";", "，", ",")):
            continue
        title = normalize_heading(line)
        if title:
            titles.append(title)
    return titles


def _segment_chunk(chunk: SourceChunk) -> list[tuple[str | None, str, int]]:
    """把一个 chunk 切成 (标题, 正文, 段内序号) 片段。

    chunk 自带 heading 时直接用它；否则按行首编号在正文里切。
    两种情况都没有时返回 [(None, 全文, 0)]，由调用方丢弃（无归属证据不可用）。

    标题后**没有正文**（纯大纲式文档、或标题紧邻标题）时，仍保留该章节并附空正文：
    标题本身就是「这份标书有这一章」的证据，直接丢弃会让纯目录结构的文档丢掉全部章节，
    并让用户看到「请确认文档带标题层级」这种与事实相反的提示。
    """
    inherited = normalize_heading(chunk.heading)
    segments: list[tuple[str | None, str, int]] = []
    current_title = inherited or None
    buffer: list[str] = []
    # 当前标题是否已产出过片段；用于判断「标题紧跟标题、无正文」时补空片段。
    emitted_for_current = False
    found_any = False

    def _emit(title: str | None, body: str) -> None:
        nonlocal emitted_for_current
        segments.append((title, body, len(segments)))
        emitted_for_current = True

    for raw_line in chunk.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        title = None
        if (
            len(line) <= MAX_TITLE_CHARS + 12
            and _has_line_numbering(line)
            and not line.endswith(("。", "；", ";", "，", ","))
        ):
            title = normalize_heading(line)
        if title:
            found_any = True
            if buffer:
                _emit(current_title, "\n".join(buffer))
                buffer = []
            elif current_title and not emitted_for_current:
                # 上一个标题没有任何正文：补一个空正文片段，保住章节骨架。
                _emit(current_title, "")
            current_title = title
            emitted_for_current = False
            continue
        buffer.append(line)
    if buffer:
        _emit(current_title, "\n".join(buffer))
    elif current_title and found_any and not emitted_for_current:
        # 文档以标题结尾且该标题无正文：同样保留。
        _emit(current_title, "")
    if not found_any:
        return [(inherited or None, chunk.text, 0)]
    return segments


def build_sections(chunks: list[SourceChunk]) -> list[dict[str, Any]]:
    """把 chunk 序列聚成章节骨架；证据不足时抛 ValidationAppError。"""
    drafts: dict[str, _SectionDraft] = {}
    source_documents: set[UUID] = set()
    for chunk in chunks:
        source_documents.add(chunk.document_id)
        for title, body, inner_index in _segment_chunk(chunk):
            if not title:
                continue
            draft = drafts.setdefault(title, _SectionDraft(title=title))
            draft.documents.add(chunk.document_id)
            draft.chunk_count += 1
            # 排序键 = (chunk 在文档中的位置, chunk 内的片段位置)：
            # 章节顺序跟随原文骨架，且与输入列表顺序无关。
            order_key = (chunk.chunk_index, inner_index)
            if draft.chunk_count == 1 or order_key < draft.order_key:
                draft.order_key = order_key
            segment_keywords = _segment_keywords(body)
            draft.keywords.update(segment_keywords)
            for token in segment_keywords:
                draft.keyword_docs.setdefault(token, set()).add(chunk.document_id)
            draft.materials.update(_material_cues(body))

    if len(source_documents) < MIN_SOURCE_DOCUMENTS:
        raise ValidationAppError(
            f"历史标书不足：至少需要 {MIN_SOURCE_DOCUMENTS} 份已入库的案例文档，"
            f"当前只有 {len(source_documents)} 份"
        )
    if len(drafts) < MIN_SECTIONS:
        # 区分两种成因，避免给用户一句与事实相反的提示：
        # - 完全没识别到标题 → 确实是文档缺少标题层级；
        # - 识别到标题但种类不够 → 是「章节太集中/重复」，不是没有标题。
        if not drafts:
            detail = "文档中未识别到任何章节标题，请确认使用「第一章 / 1.2 技术方案」这类带编号的标题行"
        else:
            detail = (
                f"已识别到标题但不同章节过少（{len(drafts)} 个："
                f"{'、'.join(sorted(drafts)[:5])}），请补充章节层级更完整的案例文档"
            )
        raise ValidationAppError(
            f"章节证据不足：至少需要 {MIN_SECTIONS} 个可识别章节，当前只有 {len(drafts)} 个；{detail}"
        )

    ranked = sorted(
        drafts.values(),
        key=lambda item: (-len(item.documents), item.order_key, item.title),
    )
    sections: list[dict[str, Any]] = []
    for draft in ranked[:MAX_SECTIONS]:
        keywords = _top_keywords(draft)
        materials = _rank_materials(draft)
        purpose = (
            f"来自 {len(draft.documents)} 份历史标书的「{draft.title}」章节、"
            f"共 {draft.chunk_count} 个段落"
        )
        if keywords:
            purpose += f"；高频要点：{'、'.join(keywords)}"
        purpose += "。"
        guidance = "保留历史标书的编排顺序与响应口径，逐条对应当前招标文件要求"
        if keywords:
            guidance += f"；重点覆盖 {'、'.join(keywords)}"
        if materials:
            guidance += f"；需随附：{'、'.join(materials)}"
        guidance += "。"
        sections.append(
            {
                "title": draft.title,
                "section_type": section_type_for(draft.title),
                "purpose": purpose,
                "guidance": guidance,
                "required_materials": materials,
            }
        )
    return sections


def draft_name(kb_name: str, sections: list[dict[str, Any]], document_count: int) -> str:
    return f"{kb_name} · 历史标书结构模板（{document_count} 份 / {len(sections)} 章）"


async def load_source_chunks(
    session: AsyncSession, user: CurrentUser, kb_id: UUID
) -> tuple[list[SourceChunk], int]:
    """读取该知识库里 case 类/历史标书文档的 chunk。"""
    documents = list(
        (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == user.tenant_id,
                    KnowledgeDocument.kb_id == kb_id,
                    KnowledgeDocument.status == "parsed",
                    or_(
                        KnowledgeDocument.kind == "case",
                        KnowledgeDocument.tags.contains([HISTORY_TAG]),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    # 文档级 ACL：KB 写权限不等于可读全部文档；草稿会以 tenant 作用域发布，
    # 未授权文档的标题/结构不得进入派生数据。
    documents = await filter_documents(session, documents, user, "read")
    if not documents:
        return [], 0
    titles = {document.id: document.title for document in documents}
    rows = list(
        (
            await session.execute(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.tenant_id == user.tenant_id,
                    KnowledgeChunk.document_id.in_(titles),
                )
                .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    chunks = [
        SourceChunk(
            document_id=row.document_id,
            document_title=titles.get(row.document_id, ""),
            chunk_index=row.chunk_index,
            heading=row.heading,
            text=row.text or "",
            keywords=tuple(str(keyword) for keyword in (row.keywords or [])),
        )
        for row in rows
    ]
    return chunks, len(documents)


async def generate_template_draft(
    session: AsyncSession,
    user: CurrentUser,
    kb_id: UUID,
) -> KnowledgePack:
    """基于知识库内历史标书生成结构模板草稿（draft，不自动启用）。"""
    kb_row = (
        await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == user.tenant_id, KnowledgeBase.id == kb_id
            )
        )
    ).scalar_one_or_none()
    if kb_row is None:
        raise NotFoundError()
    if not await can_access_kb(session, kb_row, user, "write"):
        # 无权访问的知识库按不存在处理，不泄露存在性。
        raise NotFoundError()

    chunks, document_count = await load_source_chunks(session, user, kb_id)
    sections = build_sections(chunks)
    pack = KnowledgePack(
        tenant_id=user.tenant_id,
        code=f"generated.{uuid4().hex[:10]}",
        name=draft_name(kb_row.name, sections, document_count),
        category="structure",
        description=(
            f"由知识库「{kb_row.name}」中 {document_count} 份历史标书确定性抽取的章节骨架；"
            "草稿需人工确认后发布启用。"
        ),
        content_json={
            "sections": sections,
            "generated_from": {
                "kb_id": str(kb_id),
                "document_count": document_count,
                "chunk_count": len(chunks),
            },
        },
        version="1.0",
        is_builtin=False,
        enabled=True,
        scope="tenant",
        classification=kb_row.classification,
        status="draft",
        owner_status="active",
        published_by=user.id,
        published_at=None,
    )
    session.add(pack)
    await session.commit()
    await session.refresh(pack)
    return pack
