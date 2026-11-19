import pytest

from app.modules.generation.service import _sanitize_blocks
from app.modules.knowledge.kb import _chunks, _image_dimensions, _tokens, section_query

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_chinese_tokens_include_bigrams() -> None:
    tokens = _tokens("政务云安全运维")
    assert "政务" in tokens
    assert "运维" in tokens
    assert "政务云安全运维" not in tokens


def test_chunks_are_bounded_and_keep_heading() -> None:
    text = "# 产品说明\n" + "\n".join("星云平台支持双活架构与自动巡检。" for _ in range(120))
    chunks = _chunks(text)
    assert chunks
    assert all(len(chunk) < 1000 for chunk, _ in chunks)
    assert all(heading == "# 产品说明" for _, heading in chunks)


def test_image_dimensions_reads_png() -> None:
    assert _image_dimensions(PNG) == (1, 1)


def test_sanitize_blocks_keeps_only_known_images() -> None:
    blocks = [
        {"type": "paragraph", "text": "正文"},
        {"type": "table", "caption": "证据表", "headers": ["项", "值"], "rows": [["A", "1"]]},
        {"type": "image", "image_id": "known", "caption": "架构图"},
        {"type": "image", "image_id": "invented", "caption": "编造图"},
    ]
    cleaned = _sanitize_blocks(blocks, {"known"})
    assert [block["type"] for block in cleaned] == ["paragraph", "table", "image"]
    assert cleaned[1]["caption"] == "证据表"
    assert cleaned[-1]["image_id"] == "known"


@pytest.mark.asyncio
async def test_llm_blocks_injects_knowledge_and_rejects_unknown_images() -> None:
    import json
    from types import SimpleNamespace
    from uuid import uuid4

    from app.modules.generation.service import _llm_blocks

    class Capture:
        def __init__(self) -> None:
            self.user = ""

        async def complete(self, *, system: str, user: str) -> str:
            self.user = user
            return json.dumps(
                {
                    "blocks": [
                        {"type": "paragraph", "text": "基于知识库的技术方案。"},
                        {"type": "image", "image_id": "invented", "caption": "编造图"},
                        {"type": "image", "image_id": "img-1", "caption": "双活架构"},
                    ]
                },
                ensure_ascii=False,
            )

    client = Capture()
    section = SimpleNamespace(
        id=uuid4(), title="技术方案", section_type="technical", scoring_item_id=None
    )
    project = SimpleNamespace(name="某项目", buyer="某采购人", code="P-001", bid_deadline=None)
    knowledge = {
        "chunks": [{"title": "产品说明", "chunk": "星云平台支持双活架构", "score": 2.0}],
        "images": [
            {"id": "img-1", "title": "双活架构图", "tags": ["架构图"], "caption": "双活"}
        ],
    }
    blocks = await _llm_blocks(client, section, project, {}, {}, None, {}, knowledge)
    assert "星云平台支持双活架构" in client.user
    assert "双活架构图" in client.user
    assert [block["type"] for block in blocks] == ["paragraph", "image"]
    assert blocks[-1]["image_id"] == "img-1"


def test_retrieval_diversity_and_threshold() -> None:

    from app.modules.knowledge.kb import _select_hits

    ranked = [
        {"document_id": "a", "score": 10.0, "title": "A", "chunk": "a1"},
        {"document_id": "a", "score": 9.5, "title": "A", "chunk": "a2"},
        {"document_id": "a", "score": 9.0, "title": "A", "chunk": "a3"},
        {"document_id": "b", "score": 8.0, "title": "B", "chunk": "b1"},
        {"document_id": "b", "score": 7.0, "title": "B", "chunk": "b2"},
        {"document_id": "c", "score": 1.0, "title": "C", "chunk": "c1"},
    ]
    selected = _select_hits(ranked, 4)
    assert [hit["chunk"] for hit in selected] == ["a1", "a2", "b1", "b2"]

    assert len(_select_hits([{"document_id": "x", "score": 10.0}, {"document_id": "y", "score": 1.0}], 5)) == 1


def test_retrieval_tag_boost() -> None:
    from types import SimpleNamespace

    from app.modules.knowledge.kb import _score_chunks, _score_images

    def chunk(document_id: str, text: str):
        return SimpleNamespace(
            document_id=document_id,
            keywords=["运维", "平台"],
            text=text,
            heading=None,
        )

    rows = [
        (chunk("plain", "普通文档"), "普通文档", []),
        (chunk("tagged", "标签文档"), "标签文档", ["运维平台"]),
    ]
    ranked = _score_chunks(["运维", "平台"], rows)
    assert ranked[0]["document_id"] == "tagged"
    assert ranked[0]["score"] > ranked[1]["score"]

    title_only = _score_chunks(
        ["双活", "架构"],
        [(chunk("title", "正文只写高可用"), "双活架构方案", [])],
    )
    assert title_only
    assert title_only[0]["document_id"] == "title"
    assert title_only[0]["score"] > 0

    def image(title: str, caption: str | None = None, tags: list[str] | None = None, sensitive: bool = False):
        return SimpleNamespace(
            id=title,
            title=title,
            caption=caption,
            tags=tags or [],
            filename="image.png",
            content_type="image/png",
            size_bytes=1,
            width=1,
            height=1,
            sensitive=sensitive,
            created_at=None,
        )

    ranked_images = _score_images(
        ["架构", "构图"],
        [image("双活架构图", "双活", ["架构图"]), image("项目验收现场", "现场", ["案例"])],
        include_sensitive=True,
        limit=4,
    )
    assert [item["title"] for item in ranked_images] == ["双活架构图"]
    assert ranked_images[0]["score"] > 0

    strong = image("alpha beta gamma delta epsilon")
    weak = image("", "alpha", [])
    filtered = _score_images(
        ["alpha", "beta", "gamma", "delta", "epsilon"],
        [strong, weak],
        include_sensitive=True,
        limit=4,
    )
    assert [item["title"] for item in filtered] == ["alpha beta gamma delta epsilon"]

    hidden = _score_images(
        ["架构", "构图"],
        [image("双活架构图", sensitive=True)],
        include_sensitive=False,
        limit=4,
    )
    assert hidden == []

    assert (
        section_query(
            title="技术方案",
            scoring_text="双活架构 10分",
            project_name="某项目",
            buyer="某采购人",
            code="P-1",
        )
        == "技术方案 双活架构 10分 某项目 某采购人 P-1"
    )
    assert section_query(title="技术方案", scoring_text=" ", project_name="某项目") == "技术方案 某项目"


async def test_llm_blocks_injects_global_facts_so_bidder_name_is_known() -> None:
    import json
    from types import SimpleNamespace
    from uuid import uuid4

    from app.modules.generation.service import _llm_blocks

    class Capture:
        def __init__(self) -> None:
            self.system = ""
            self.user = ""

        async def complete(self, *, system: str, user: str) -> str:
            self.system, self.user = system, user
            return json.dumps({"blocks": [{"type": "paragraph", "text": "技术方案。"}]})

    client = Capture()
    section = SimpleNamespace(
        id=uuid4(), title="技术方案", section_type="technical", scoring_item_id=None
    )
    project = SimpleNamespace(name="某政务云项目", buyer="某采购人", code="P-001", bid_deadline=None)
    assets = {
        "qualifications": [{"name": "涉密信息系统集成乙级"}],
        "personnel": [{"name": "张三"}],
        "performances": [{"project_name": "某市政务云运维"}],
    }
    await _llm_blocks(
        client, section, project, {}, {}, None, assets, None, company_name="演示系统集成有限公司"
    )

    payload = json.loads(client.user)
    facts = payload["global_facts"]
    assert facts["bidder_name"] == "演示系统集成有限公司"
    assert facts["project_name"] == "某政务云项目"
    assert facts["qualifications"] == ["涉密信息系统集成乙级"]
    assert facts["performances"] == ["某市政务云运维"]
    assert "global_facts" in client.system


def test_search_applies_kb_filter_before_scan_limit() -> None:
    """kb_id 过滤必须在 .limit(MAX_SCAN_CHUNKS) 之前。

    否则先截断再过滤，指定库的块会被其他库挤出窗口，表现为「库里明明有
    文档却检索不到」。这里用源码顺序断言，避免有人重排语句时又改回去。
    """
    import inspect

    from app.modules.knowledge import kb as kb_module

    source = inspect.getsource(kb_module.search)
    filter_at = source.index("KnowledgeChunk.kb_id == kb_id")
    limit_at = source.index("statement = statement.limit(MAX_SCAN_CHUNKS)")
    assert filter_at < limit_at
