import asyncio
import base64
from pathlib import Path

import pytest

from app.modules.tender.ocr import (
    MineruOcrProvider,
    MinimaxOcrProvider,
    MiniMaxVisionClient,
    OcrModelMissingError,
    OcrResponseError,
    OcrTransportError,
    UnsupportedDocumentError,
    _html_table_to_rows,
    _items_to_ocr_result,
    build_ocr_provider,
    mineru_available,
    parsed_from_ocr,
    resolve_mineru_tier,
)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _settings(**overrides):
    from types import SimpleNamespace

    base = {
        "OCR_PROVIDER": "auto",
        "OCR_TIMEOUT_SECONDS": 120.0,
        "MINERU_TIER": "basic",
        "MINERU_TIMEOUT_SECONDS": 300.0,
        "MINIMAX_API_KEY": None,
        "MINIMAX_API_HOST": None,
        "MINIMAX_OCR_TIMEOUT_SECONDS": 120.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_factory_auto_prefers_minimax_then_falls_back_to_mineru(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.tender.ocr.mineru_available", lambda: True)

    configured = build_ocr_provider(
        _settings(MINIMAX_API_KEY="key", MINIMAX_API_HOST="https://minimax.test")
    )
    assert isinstance(configured, MinimaxOcrProvider)

    fallback = build_ocr_provider(_settings())
    assert isinstance(fallback, MineruOcrProvider)
    assert fallback.tier == "basic"
    assert fallback.timeout_seconds == 300.0

    tuned = build_ocr_provider(
        _settings(MINERU_TIER="standard", MINERU_TIMEOUT_SECONDS=900.0)
    )
    assert isinstance(tuned, MineruOcrProvider)
    assert tuned.tier == "standard"
    assert tuned.timeout_seconds == 900.0

    monkeypatch.setattr("app.modules.tender.ocr.mineru_available", lambda: False)
    assert build_ocr_provider(_settings()) is None


def test_factory_explicit_choices(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.tender.ocr.mineru_available", lambda: True)

    assert build_ocr_provider(_settings(OCR_PROVIDER="none")) is None

    # 显式 minimax 但未配置：返回 None（误配置经 ai-status 透出，不在依赖构建抛错）
    assert build_ocr_provider(_settings(OCR_PROVIDER="minimax")) is None

    minimax = build_ocr_provider(
        _settings(OCR_PROVIDER="minimax", MINIMAX_API_KEY="k", MINIMAX_API_HOST="h")
    )
    assert isinstance(minimax, MinimaxOcrProvider)

    mineru = build_ocr_provider(_settings(OCR_PROVIDER="mineru"))
    assert isinstance(mineru, MineruOcrProvider)

    # 未知 choice（含已移除的 paddle）：工厂不抛错，返回 None
    assert build_ocr_provider(_settings(OCR_PROVIDER="paddle")) is None

    monkeypatch.setattr("app.modules.tender.ocr.mineru_available", lambda: False)
    assert build_ocr_provider(_settings(OCR_PROVIDER="mineru")) is None


def test_mineru_available_uses_find_spec_without_import(monkeypatch) -> None:
    from importlib.machinery import ModuleSpec

    monkeypatch.setattr(
        "app.modules.tender.ocr.find_spec",
        lambda name: ModuleSpec(name, None) if name == "mineru" else None,
    )
    assert mineru_available() is True

    monkeypatch.setattr("app.modules.tender.ocr.find_spec", lambda name: None)
    assert mineru_available() is False


def test_minimax_provider_recognizes_image_and_reports_no_tables() -> None:
    provider = MinimaxOcrProvider(
        MiniMaxVisionClient(api_key="k", api_host="https://minimax.test")
    )
    assert provider.configured() is True
    unconfigured = MinimaxOcrProvider(MiniMaxVisionClient(api_key=None, api_host=None))
    assert unconfigured.configured() is False


def test_html_table_parser_extracts_rows_and_tolerates_inline_tags() -> None:
    rows = _html_table_to_rows(
        "<html><body><table><tr><td>评分项</td><td>分值</td></tr>"
        "<tr><td>技术<b>方案</b></td><td>20<br/>分</td></tr></table></body></html>"
    )
    assert rows == [
        ["评分项", "分值"],
        ["技术方案", "20分"],
    ]


def test_items_to_ocr_result_maps_pages_tables_and_skips_images() -> None:
    items = [
        {"type": "text", "text": "资格要求：投标人须具备资质", "page_idx": 0},
        {"type": "equation", "text": "E=mc^2", "page_idx": 0},
        {"type": "image", "img_path": "images/0.jpg", "page_idx": 0},
        {
            "type": "table",
            "table_body": "<table><tr><td>评分项</td><td>分值</td></tr>"
            "<tr><td>技术方案</td><td>20分</td></tr></table>",
            "table_caption": ["评分表"],
            "page_idx": 1,
        },
        {"type": "table", "table_body": "   ", "page_idx": 1},
        {"type": "text", "text": "预算金额：480万元", "page_idx": 2},
        "not-a-dict",
    ]
    result = _items_to_ocr_result(items, "mineru")

    assert result.provider == "mineru"
    assert result.structure_used is True
    assert [page.page_no for page in result.pages] == [1, 2, 3]
    assert result.pages[0].text == "资格要求：投标人须具备资质\nE=mc^2"
    assert result.pages[2].text == "预算金额：480万元"
    assert len(result.tables) == 1
    assert result.tables[0].page == 2
    assert result.tables[0].rows == [["评分项", "分值"], ["技术方案", "20分"]]

    parsed = parsed_from_ocr(result)
    kinds = [(block.kind, block.text) for block in parsed.blocks]
    assert ("paragraph", "资格要求：投标人须具备资质") in kinds
    assert ("table_cell", "评分项") in kinds
    header = next(block for block in parsed.blocks if block.text == "评分项")
    assert header.header is True
    assert header.table_index == 0
    assert header.row == 0 and header.col == 0
    assert header.page == 2
    tech = next(block for block in parsed.blocks if block.text == "20分")
    assert tech.row == 1 and tech.col == 1 and tech.header is False


def test_items_to_ocr_result_empty_yields_empty_pages() -> None:
    result = _items_to_ocr_result(
        [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], "mineru"
    )
    assert result.pages == []
    assert result.tables == []
    assert result.structure_used is False


class _FakeMineruParser:
    def __init__(
        self,
        *,
        result: object | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def parse_async(self, path, *, page_range=""):  # noqa: ANN001 - test double
        self.calls.append((str(path), page_range))
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _install_items(monkeypatch, items: list) -> None:
    monkeypatch.setattr(
        "app.modules.tender.ocr._extract_items", lambda result: items
    )


async def test_mineru_recognize_pdf_passes_page_range_and_cleans_tmp(
    monkeypatch,
) -> None:
    parser = _FakeMineruParser(result=object())
    provider = MineruOcrProvider(parser_factory=lambda: parser)
    _install_items(
        monkeypatch,
        [{"type": "text", "text": "招标公告", "page_idx": 0}],
    )

    result = await provider.recognize(b"%PDF-fake", "scan.pdf", max_pages=5)

    assert result.provider == "mineru"
    assert result.pages[0].page_no == 1
    assert result.pages[0].text == "招标公告"
    assert len(parser.calls) == 1
    assert parser.calls[0][1] == "1-5"
    assert Path(parser.calls[0][0]).exists() is False  # 临时文件已清理


async def test_mineru_recognize_image_skips_page_range(monkeypatch) -> None:
    parser = _FakeMineruParser(result=object())
    provider = MineruOcrProvider(parser_factory=lambda: parser)
    _install_items(monkeypatch, [])

    await provider.recognize(PNG_BYTES, "scan.png", max_pages=5)

    assert parser.calls[0][1] == ""


async def test_mineru_recognize_rejects_unsupported_and_empty(monkeypatch) -> None:
    provider = MineruOcrProvider(parser_factory=lambda: _FakeMineruParser())
    with pytest.raises(UnsupportedDocumentError):
        await provider.recognize(b"plain", "doc.txt", max_pages=5)
    with pytest.raises(OcrResponseError):
        await provider.recognize(b"", "scan.png", max_pages=5)


async def test_mineru_parse_failure_wraps_as_response_error(monkeypatch) -> None:
    parser = _FakeMineruParser(error=RuntimeError("model exploded"))
    provider = MineruOcrProvider(parser_factory=lambda: parser)
    _install_items(monkeypatch, [])
    with pytest.raises(OcrResponseError, match="mineru OCR failed"):
        await provider.recognize(PNG_BYTES, "scan.png", max_pages=5)


async def test_mineru_timeout_wraps_as_transport_error() -> None:
    parser = _FakeMineruParser(delay=1.0)
    provider = MineruOcrProvider(
        timeout_seconds=0.05, parser_factory=lambda: parser
    )
    with pytest.raises(OcrTransportError, match="timed out"):
        await provider.recognize(PNG_BYTES, "scan.png", max_pages=5)


async def test_mineru_missing_runtime_raises_model_missing() -> None:
    provider = MineruOcrProvider(
        parser_factory=lambda: (_ for _ in ()).throw(ImportError("no mineru"))
    )
    with pytest.raises(OcrModelMissingError):
        await provider.recognize(PNG_BYTES, "scan.png", max_pages=5)


def test_mineru_parser_defaults_to_ocr_mode(monkeypatch) -> None:
    """无 factory 注入时，_get_parser 以 tier/parse_mode="ocr"/image_analysis=False 构建。"""
    import sys
    import types

    captured: dict = {}

    class _MinerUParser:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    fake_module = types.ModuleType("mineru.parser")
    fake_module.MinerUParser = _MinerUParser
    monkeypatch.setitem(sys.modules, "mineru.parser", fake_module)

    provider = MineruOcrProvider(tier="standard")
    parser = provider._get_parser(provider.tier)

    assert isinstance(parser, _MinerUParser)
    assert captured == {
        "tier": "standard",
        "parse_mode": "ocr",
        "image_analysis": False,
    }


def test_resolve_tier_explicit_passes_through() -> None:
    assert resolve_mineru_tier("basic", min_vram_gb=8.0) == "basic"
    assert resolve_mineru_tier("standard", min_vram_gb=8.0) == "standard"
    assert resolve_mineru_tier("flash", min_vram_gb=8.0) == "flash"


def test_resolve_tier_auto_by_vram(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", lambda: 24.0)
    assert resolve_mineru_tier("auto", min_vram_gb=8.0) == "standard"
    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", lambda: 4.0)
    assert resolve_mineru_tier("auto", min_vram_gb=8.0) == "basic"
    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", lambda: None)
    assert resolve_mineru_tier("auto", min_vram_gb=8.0) == "basic"


def test_resolve_tier_auto_threshold_boundary(monkeypatch) -> None:
    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", lambda: 8.0)
    assert resolve_mineru_tier("auto", min_vram_gb=8.0) == "standard"
    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", lambda: 7.9)
    assert resolve_mineru_tier("auto", min_vram_gb=8.0) == "basic"


async def test_mineru_auto_tier_resolves_once_and_caches(monkeypatch) -> None:
    calls: list[int] = []

    def fake_detect() -> None:
        calls.append(1)

    monkeypatch.setattr("app.modules.tender.ocr.detect_gpu_vram_gb", fake_detect)
    provider = MineruOcrProvider(
        tier="auto", parser_factory=lambda: _FakeMineruParser(result=object())
    )
    _install_items(monkeypatch, [])

    await provider.recognize(PNG_BYTES, "a.png", max_pages=5)
    await provider.recognize(PNG_BYTES, "b.png", max_pages=5)

    assert len(calls) == 1
    assert provider.effective_tier == "basic"


async def test_mineru_accepts_full_mineru_suffixes(monkeypatch) -> None:
    parser = _FakeMineruParser(result=object())
    provider = MineruOcrProvider(parser_factory=lambda: parser)
    _install_items(monkeypatch, [])

    for filename in ("t.pptx", "t.xlsx", "t.epub", "t.ofd", "t.html", "t.doc", "t.rtf"):
        await provider.recognize(b"x", filename, max_pages=5)

    with pytest.raises(UnsupportedDocumentError):
        await provider.recognize(b"x", "t.txt", max_pages=5)
