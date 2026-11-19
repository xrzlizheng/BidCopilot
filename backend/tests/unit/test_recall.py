from types import SimpleNamespace

from app.modules.evaluation.recall import (
    ITEM_TYPE_OBJECTIVE,
    ITEM_TYPE_QUALIFICATION,
    ITEM_TYPE_SUBJECTIVE,
    classify_item,
    recall_for_items,
    select_within_budget,
    split_section_text,
    split_sections,
    tokens,
)


def _section(
    section_id: str,
    title: str,
    text: str,
    order: int = 1,
    blocks: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=section_id,
        title=title,
        order_index=order,
        content_text=text,
        content_json=blocks or [],
    )


def _item(
    item_id: str, text: str, criteria: str | None = None, category: str = "技术"
) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, item=text, criteria=criteria, category=category)


def test_tokens_uses_bigrams_and_drops_stopwords() -> None:
    values = tokens("技术方案提供支持")

    assert "技术" in values
    assert "术方" in values
    # 虚词进停用词表；名词类高频词（方案）保留，交给 IDF 降权。
    assert "提供" not in values
    assert "方案" in values


def test_split_sections_merges_short_lines() -> None:
    text = "第一行很短\n第二行也很短\n\n" + "长内容" * 40

    paragraphs = split_sections([_section("s1", "技术方案", text)])

    assert len(paragraphs) == 2
    assert "第一行很短" in paragraphs[0].text
    assert "第二行也很短" in paragraphs[0].text
    assert paragraphs[0].section_title == "技术方案"


def test_split_sections_splits_overlong_block_on_period() -> None:
    text = "这是一个很长的句子。" * 80

    paragraphs = split_sections([_section("s1", "长章节", text)])

    assert len(paragraphs) >= 2
    assert all(len(paragraph.text) <= 601 for paragraph in paragraphs)


def test_split_sections_skips_blank_sections() -> None:
    paragraphs = split_sections([_section("s1", "空章", "   \n  ")])

    assert paragraphs == []


def test_recall_prefers_title_hit() -> None:
    sections = [
        _section("s1", "项目业绩", "政务云平台建设经验，合同金额一千二百万。", 1),
        _section("s2", "技术方案", "技术方案采用微服务架构，支持弹性扩容。", 2),
    ]
    paragraphs = split_sections(sections)

    result = recall_for_items([_item("i1", "技术方案架构")], paragraphs)

    picked = result["i1"]
    assert picked
    assert picked[0]["section"] == "技术方案"


def test_recall_returns_empty_without_token_overlap() -> None:
    paragraphs = split_sections([_section("s1", "其他", "甲甲公司乙乙内容。")])

    result = recall_for_items([_item("i1", "qqqq")], paragraphs)

    assert result["i1"] == []


def test_recall_caps_paragraph_count_and_chars() -> None:
    text = "\n".join(f"技术方案第{index}段内容。" for index in range(30))
    paragraphs = split_sections([_section("s1", "技术方案", text)])

    result = recall_for_items(
        [_item("i1", "技术方案")], paragraphs, per_item=3, max_chars=120
    )

    picked = result["i1"]
    assert 0 < len(picked) <= 3
    assert sum(len(entry["text"]) for entry in picked) <= 120


def test_recall_handles_empty_inputs() -> None:
    assert recall_for_items([], []) == {}
    paragraphs = split_sections([_section("s1", "技术方案", "技术方案内容。")])
    assert recall_for_items([_item("i1", "技术方案")], []) == {"i1": []}
    assert paragraphs


def test_select_within_budget_returns_whole_section_when_it_fits() -> None:
    paragraphs = split_sections(
        [_section("s1", "技术方案", "第一段内容说明。" * 6 + "\n" + "第二段内容说明。" * 6)]
    )

    picked, clipped = select_within_budget(paragraphs, "技术方案", 10_000)

    assert clipped is False
    assert len(picked) == len(paragraphs)


def test_select_within_budget_keeps_head_and_tail_when_over() -> None:
    lines = [f"技术方案第{index}段：{'填充论证' * 40}。" for index in range(10)]
    paragraphs = split_sections([_section("s1", "技术方案", "\n".join(lines))])

    picked, clipped = select_within_budget(paragraphs, "技术方案", 800)

    assert clipped is True
    # 超预算保头尾，结论段不会被"只取前 N 字"砍掉。
    assert any("第0段" in entry["text"] for entry in picked)
    assert any("第9段" in entry["text"] for entry in picked)
    assert sum(len(entry["text"]) for entry in picked) <= 800


def test_select_within_budget_handles_single_oversized_paragraph() -> None:
    paragraphs = split_sections([_section("s1", "技术方案", "技术" * 500)])

    picked, clipped = select_within_budget(paragraphs, "技术", 100)

    assert clipped is True
    assert len(picked) == 1
    assert len(picked[0]["text"]) == 100


def test_select_within_budget_handles_empty_input() -> None:
    assert select_within_budget([], "技术方案", 100) == ([], False)


def test_split_section_text_numbers_paragraphs_in_order() -> None:
    text = "第一段内容说明。" * 6 + "\n" + "第二段内容说明。" * 6

    paragraphs = split_section_text(_section("s1", "技术方案", text))

    # 编号用于证据回溯定位（"技术方案响应 · 第 N 段"）。
    assert [paragraph.index_in_section for paragraph in paragraphs] == [0, 1]


def test_table_blocks_keep_headers_and_kind() -> None:
    blocks = [
        {"type": "paragraph", "text": "近三年类似项目业绩如下，均已完成验收。"},
        {
            "type": "table",
            "caption": "类似项目业绩表",
            "headers": ["序号", "项目名称", "合同金额"],
            "rows": [["1", "某政务云平台", "1200万"], ["2", "某数据中台", "800万"]],
        },
    ]

    paragraphs = split_section_text(_section("s1", "项目业绩响应", "", blocks=blocks))

    table = next(paragraph for paragraph in paragraphs if paragraph.kind == "table")
    assert "表：类似项目业绩表" in table.text
    # 旧实现 _blocks_text 只把单元格用空格拼起来、丢掉表头，这里必须保留列关系。
    assert "| 序号 | 项目名称 | 合同金额 |" in table.text
    assert "| 1 | 某政务云平台 | 1200万 |" in table.text
    # 行数由代码给出，模型不必自己数——客观分靠它才数得准。
    assert "（本表共 2 行数据）" in table.text
    # 表格是可核验的硬证据，权重高于正文。
    assert table.weight > 1.0


def test_response_statement_is_marked() -> None:
    text = (
        "我方完全响应招标文件第五章的全部技术要求，无任何偏离或负偏离。\n"
        "技术方案采用微服务架构，支持容器化部署与弹性扩容，满足高可用要求。"
    )

    paragraphs = split_section_text(_section("s1", "技术方案响应", text))

    assert paragraphs[0].kind == "response_statement"
    assert paragraphs[1].kind == "paragraph"


def test_classify_item_distinguishes_types() -> None:
    assert (
        classify_item(_item("a", "类似项目业绩", criteria="每提供1个合同得2分"))
        == ITEM_TYPE_OBJECTIVE
    )
    assert classify_item(_item("b", "技术方案合理性")) == ITEM_TYPE_SUBJECTIVE
    assert (
        classify_item(_item("c", "★实质性条款响应", criteria="不满足将否决投标"))
        == ITEM_TYPE_QUALIFICATION
    )


def test_select_within_budget_prioritizes_table_evidence() -> None:
    blocks = [
        {"type": "paragraph", "text": "说明段落一。" * 10},
        {
            "type": "table",
            "caption": "业绩表",
            "headers": ["序号", "项目名称"],
            "rows": [["1", "某政务云平台"]],
        },
        {"type": "paragraph", "text": "说明段落二。" * 10},
        {"type": "paragraph", "text": "说明段落三。" * 10},
    ]
    paragraphs = split_section_text(_section("s1", "项目业绩", "", blocks=blocks))

    picked, _ = select_within_budget(paragraphs, "技术方案", 200)

    # 表格即使没命中关键词也要优先占位：客观分靠它数得清。
    assert "table" in [entry["kind"] for entry in picked]
