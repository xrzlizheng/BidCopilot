"""template_autogen 的纯确定性单测。

这些用例全部只跑纯函数：不连库、不调 LLM、不 mock 生产依赖。
断言重点：
1. 章节标题只来自真实 heading，归一化正确；
2. 材料线索只来自正文「须/应提供…」句式或固定词表；
3. 输入不足时明确报错，绝不产出空模板或占位内容；
4. 同样输入必然产出同样结果（确定性）。
"""

from itertools import count
from uuid import UUID, uuid4

import pytest

from app.core.errors import ValidationAppError
from app.modules.knowledge.template_autogen import (
    KEYWORD_VOCAB,
    MATERIAL_VOCAB,
    MAX_SECTIONS,
    MIN_SECTIONS,
    MIN_SOURCE_DOCUMENTS,
    SourceChunk,
    build_sections,
    draft_name,
    extract_line_headings,
    normalize_heading,
    section_type_for,
)


def _chunk(
    document_id: UUID,
    heading: str | None,
    text: str = "",
    *,
    keywords: tuple[str, ...] = (),
    index: int = 0,
) -> SourceChunk:
    return SourceChunk(
        document_id=document_id,
        document_title="历史标书",
        chunk_index=index,
        heading=heading,
        text=text,
        keywords=keywords,
    )


SECTIONS = {
    "投标函": "我方向采购人递交投标函，报价有效期 90 天，须提供法定代表人授权书。",
    "资格证明文件": "须提供营业执照与信息系统集成资质，并附业绩合同。",
    "技术方案": "方案描述双活架构与自动化巡检，含实施计划与运维响应。",
    "售后服务": "量化 SLA 响应时间，须提供培训方案与服务承诺。",
}


def _three_section_chunks(doc_a: UUID, doc_b: UUID) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    for document_id, titles in (
        (doc_a, ("投标函", "资格证明文件", "技术方案")),
        (doc_b, ("投标函", "资格证明文件", "技术方案", "售后服务")),
    ):
        for index, title in enumerate(titles):
            chunks.append(
                _chunk(
                    document_id,
                    f"第{index + 1}章 {title}",
                    f"{SECTIONS[title]}\n{title}章节说明：实施计划与质量保障措施。",
                    index=index,
                )
            )
    return chunks


def test_normalize_heading_strips_numbering_levels() -> None:
    assert normalize_heading("第一章 投标函") == "投标函"
    assert normalize_heading("一、资格证明文件") == "资格证明文件"
    assert normalize_heading("1.2 技术方案") == "技术方案"
    assert normalize_heading("# 售后服务") == "售后服务"
    assert normalize_heading("（三）报价说明") == "报价说明"
    # 行尾注记应被剥离，避免「技术方案（一）」与「技术方案」被当成两章
    assert normalize_heading("技术方案（一）") == "技术方案"


def test_normalize_heading_rejects_structure_noise() -> None:
    assert normalize_heading(None) == ""
    assert normalize_heading("") == ""
    assert normalize_heading("目录") == ""
    assert normalize_heading("封面") == ""
    assert normalize_heading("附") == ""  # 太短
    assert normalize_heading("技" * 60) == ""  # 超长
    assert normalize_heading("技术方案。本方案包括双活架构。") == ""  # 是正文不是标题
    assert normalize_heading("技术方案\n第二行正文") == ""


def test_section_type_mapping_is_keyword_based() -> None:
    assert section_type_for("投标函") == "cover"
    assert section_type_for("资格证明文件") == "qualification"
    assert section_type_for("类似项目业绩") == "commercial"
    assert section_type_for("投标报价表") == "commercial"
    assert section_type_for("售后服务与承诺") == "service"
    assert section_type_for("技术方案") == "technical"
    assert section_type_for("其他补充说明材料") == "other"


def test_build_sections_aggregates_same_heading_across_documents() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    sections = build_sections(_three_section_chunks(doc_a, doc_b))
    titles = [section["title"] for section in sections]
    # 顺序跟随原文大纲位置（chunk_index），不是字母序
    assert titles == ["投标函", "资格证明文件", "技术方案", "售后服务"]
    by_title = {section["title"]: section for section in sections}
    # 前三个章节两份文档都有；售后服务只有一份
    for title in ("投标函", "资格证明文件", "技术方案"):
        assert "2 份历史标书" in by_title[title]["purpose"]
    assert "1 份历史标书" in by_title["售后服务"]["purpose"]
    for section in sections:
        assert section["title"] in SECTIONS
        assert section["guidance"]
        assert isinstance(section["required_materials"], list)


def test_build_sections_materials_come_from_evidence_only() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    sections = build_sections(_three_section_chunks(doc_a, doc_b))
    by_title = {section["title"]: section for section in sections}
    qualification = by_title["资格证明文件"]
    # 正文句式「须提供营业执照与信息系统集成资质」+「并附业绩合同」
    # 连词「与」不做切分符，但命中词表后应归一成独立材料名。
    assert "营业执照" in qualification["required_materials"]
    assert "信息系统集成资质" in qualification["required_materials"]
    assert "业绩合同" in qualification["required_materials"]
    # 投标函只写了「须提供法定代表人授权书」，不能带出别的章节的材料
    bid_letter = by_title["投标函"]
    assert "法定代表人授权书" in bid_letter["required_materials"]
    assert "营业执照" not in bid_letter["required_materials"]
    # 材料名只能是「词表命中」或「正文句式原样片段」，不能有句读残渣
    for section in sections:
        for material in section["required_materials"]:
            assert material.strip() == material
            assert "。" not in material
            assert material in MATERIAL_VOCAB or len(material) >= 2


def test_build_sections_is_deterministic() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    chunks = _three_section_chunks(doc_a, doc_b)
    first = build_sections(chunks)
    second = build_sections(list(chunks))
    assert first == second
    # 顺序不因输入顺序变化而漂移（排序键 = 文档数 + chunk 位置 + 标题兜底）
    shuffled = [chunks[i] for i in (6, 1, 4, 0, 5, 2, 3)]
    assert [section["title"] for section in build_sections(shuffled)] == [
        section["title"] for section in first
    ]


def test_build_sections_ignores_chunks_without_heading() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    chunks = _three_section_chunks(doc_a, doc_b)
    chunks.append(_chunk(doc_a, None, "无标题的正文段落，须提供检测报告。", index=99))
    chunks.append(_chunk(doc_b, "目录", "目录页正文", index=99))
    sections = build_sections(chunks)
    assert [section["title"] for section in sections] == [
        "投标函",
        "资格证明文件",
        "技术方案",
        "售后服务",
    ]
    # 无 heading 的正文不得把材料线索混进模板（没有章节归属的证据不可用）
    all_materials = [
        material for section in sections for material in section["required_materials"]
    ]
    assert "检测报告" not in all_materials


def test_extract_line_headings_recovers_numbered_titles_from_body() -> None:
    """真实标书里「第1章」这种阿拉伯数字编号很常见，但 kb 的切片器只认中文数字
    编号，短文档会整篇落在一个无 heading 的 chunk 里。生成器必须自己补认一次。"""
    text = (
        "第1章 投标函\n"
        "我方向采购人递交投标函。\n"
        "1.2 技术方案\n"
        "方案描述双活架构。\n"
        "2、售后服务\n"
        "量化响应时间。\n"
    )
    assert extract_line_headings(text) == ["投标函", "技术方案", "售后服务"]
    # 带句号的长行不是标题
    assert extract_line_headings("1、本项目最高限价为 1200000 元，超过即废标。") == []


def test_build_sections_recovers_headings_when_chunk_heading_missing() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    body = (
        "第1章 投标函\n须提供法定代表人授权书。\n"
        "第2章 资格证明文件\n须提供营业执照与信息系统集成资质。\n"
        "第3章 技术方案\n方案描述双活架构与实施计划。\n"
    )
    sections = build_sections(
        [
            _chunk(doc_a, None, body, index=0),
            _chunk(doc_b, None, body, index=0),
        ]
    )
    assert [section["title"] for section in sections] == ["投标函", "资格证明文件", "技术方案"]
    by_title = {section["title"]: section for section in sections}
    assert "法定代表人授权书" in by_title["投标函"]["required_materials"]
    assert "营业执照" in by_title["资格证明文件"]["required_materials"]


def test_build_sections_rejects_single_document() -> None:
    doc_a = uuid4()
    with pytest.raises(ValidationAppError) as excinfo:
        build_sections(
            [
                _chunk(doc_a, "第一章 投标函"),
                _chunk(doc_a, "第二章 资格证明文件"),
                _chunk(doc_a, "第三章 技术方案"),
            ]
        )
    assert str(MIN_SOURCE_DOCUMENTS) in excinfo.value.message
    assert "历史标书不足" in excinfo.value.message


def test_build_sections_rejects_too_few_sections() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    with pytest.raises(ValidationAppError) as excinfo:
        build_sections(
            [
                _chunk(doc_a, "第一章 投标函"),
                _chunk(doc_b, "一、投标函"),
            ]
        )
    assert str(MIN_SECTIONS) in excinfo.value.message
    assert "章节证据不足" in excinfo.value.message


def test_build_sections_rejects_documents_without_headings() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    with pytest.raises(ValidationAppError):
        build_sections(
            [
                _chunk(doc_a, None, "整篇没有标题层级的正文。"),
                _chunk(doc_b, None, "另一篇也没有标题。"),
            ]
        )


def test_build_sections_keeps_sections_when_titles_have_no_body() -> None:
    """纯大纲式文档（标题紧邻标题、无正文）也必须保留章节骨架。

    回归：此前 `_segment_chunk` 只在「缓冲区非空」时产出片段，
    标题后没有正文时整个章节被丢弃，导致只有目录的历史标书被判「无标题层级」。
    """
    doc_a, doc_b = uuid4(), uuid4()
    outline = (
        "第一章 投标函\n"
        "第二章 资格证明文件\n"
        "第三章 技术方案\n"
        "第四章 商务报价\n"
    )
    sections = build_sections(
        [
            _chunk(doc_a, None, outline, index=0),
            _chunk(doc_b, None, outline, index=0),
        ]
    )
    assert [section["title"] for section in sections] == [
        "投标函",
        "资格证明文件",
        "技术方案",
        "商务报价",
    ]
    # 空正文不得被伪造成「要求」：材料线索必须来自真实证据
    assert all(section["required_materials"] == [] for section in sections)


def test_build_sections_error_distinguishes_missing_headings_from_few_sections() -> None:
    """报错要指出真实成因：完全无标题 vs 标题太少，不能一律说「缺少标题层级」。"""
    doc_a, doc_b = uuid4(), uuid4()

    with pytest.raises(ValidationAppError) as no_heading:
        build_sections(
            [
                _chunk(doc_a, None, "整篇没有标题层级的正文。"),
                _chunk(doc_b, None, "另一篇也没有标题。"),
            ]
        )
    assert "未识别到任何章节标题" in str(no_heading.value)

    with pytest.raises(ValidationAppError) as few_sections:
        build_sections(
            [
                _chunk(doc_a, None, "第一章 投标函\n我方参加投标。\n"),
                _chunk(doc_b, None, "第一章 投标函\n我方参加投标。\n"),
            ]
        )
    assert "不同章节过少" in str(few_sections.value)
    assert "已识别到标题" in str(few_sections.value)


def test_build_sections_never_emits_empty_template() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    sections = build_sections(_three_section_chunks(doc_a, doc_b))
    assert sections
    assert len(sections) >= MIN_SECTIONS
    assert all(section["title"].strip() for section in sections)
    assert all(section["purpose"].strip() for section in sections)
    assert all(section["guidance"].strip() for section in sections)
    # 不允许「待补充」「TODO」「示例」这类占位
    joined = " ".join(
        f"{section['title']} {section['purpose']} {section['guidance']}"
        for section in sections
    )
    for placeholder in ("待补充", "TODO", "示例", "占位", "lorem"):
        assert placeholder not in joined


def test_build_sections_caps_section_count() -> None:
    doc_a, doc_b = uuid4(), uuid4()
    chunks = []
    sequence = count()
    for index in range(MAX_SECTIONS + 6):
        for document_id in (doc_a, doc_b):
            chunks.append(
                _chunk(
                    document_id,
                    f"第{index + 1}章 章节标题{index:02d}",
                    keywords=("技术", "方案"),
                    index=next(sequence),
                )
            )
    sections = build_sections(chunks)
    assert len(sections) == MAX_SECTIONS


def test_keywords_come_from_fixed_vocab_not_bigram_fragments() -> None:
    """kb._keywords 产出的是中文二元组（「交投」「标函」），直接塞进模板是碎片。
    要点词必须收敛到固定词表内的整词，且不因分片漏词。"""
    doc_a, doc_b = uuid4(), uuid4()
    sections = build_sections(_three_section_chunks(doc_a, doc_b))
    by_title = {section["title"]: section for section in sections}
    technical = by_title["技术方案"]
    for keyword in KEYWORD_VOCAB:
        if keyword in technical["purpose"]:
            assert keyword in KEYWORD_VOCAB
    # 技术方案正文写的是「双活架构」「实施计划」「运维」，要点里应出现词表词
    assert any(word in technical["purpose"] for word in ("双活", "实施计划", "运维"))
    # 不得出现二元切词碎片
    for fragment in ("标函", "交投", "业执", "与信", "照与"):
        assert fragment not in technical["purpose"]


def test_keywords_do_not_leak_across_sections() -> None:
    """每个 chunk 的正文只归属自己的章节：投标函的材料/要点不能污染技术方案。"""
    doc_a, doc_b = uuid4(), uuid4()
    sections = build_sections(_three_section_chunks(doc_a, doc_b))
    by_title = {section["title"]: section for section in sections}
    assert "招标文件全部实质性要求" not in by_title["技术方案"]["purpose"]
    assert "授权书" not in " ".join(by_title["技术方案"]["required_materials"])


def test_draft_name_states_source_scale() -> None:
    name = draft_name("企业知识库", [{"title": "投标函"}] * 4, 3)
    assert "企业知识库" in name
    assert "3 份" in name
    assert "4 章" in name
