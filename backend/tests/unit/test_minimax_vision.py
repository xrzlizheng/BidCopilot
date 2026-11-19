import io
import json

import httpx
import pytest
import respx
from reportlab.pdfgen import canvas

from app.modules.tender.ocr import (
    MinimaxOcrProvider,
    MiniMaxVisionClient,
    OcrQuotaError,
    OcrResponseError,
    ocr_document,
    render_pdf_pages,
)


class FakeVisionClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def extract_text(self, image: bytes, *, mime_type: str = "image/png") -> str:
        self.calls += 1
        assert image
        return self.text


def _pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 720, "tender page one")
    document.showPage()
    document.save()
    return buffer.getvalue()


@respx.mock
async def test_vision_client_posts_data_url_and_rotates_keys() -> None:
    route = respx.post("https://minimax.test/v1/coding_plan/vlm").mock(
        return_value=httpx.Response(
            200,
            json={"content": "提取文字", "base_resp": {"status_code": 0}},
        )
    )
    client = MiniMaxVisionClient(
        api_key="key-one,key-two",
        api_host="https://minimax.test",
        timeout_seconds=1,
    )

    first = await client.extract_text(b"png-bytes")
    second = await client.extract_text(b"png-bytes")

    assert first == "提取文字"
    assert second == "提取文字"
    payload = json.loads(route.calls[0].request.content)
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert route.calls[0].request.headers["Authorization"] == "Bearer key-one"
    assert route.calls[1].request.headers["Authorization"] == "Bearer key-two"


@respx.mock
async def test_vision_client_maps_quota_and_empty_response() -> None:
    respx.post("https://minimax.test/v1/coding_plan/vlm").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": "",
                "base_resp": {"status_code": 1008, "status_msg": "insufficient balance"},
            },
        )
    )
    client = MiniMaxVisionClient(api_key="key", api_host="https://minimax.test")

    with pytest.raises(OcrQuotaError):
        await client.extract_text(b"png")


@respx.mock
async def test_vision_client_rejects_empty_content() -> None:
    respx.post("https://minimax.test/v1/coding_plan/vlm").mock(
        return_value=httpx.Response(
            200,
            json={"content": "", "base_resp": {"status_code": 0}},
        )
    )
    client = MiniMaxVisionClient(api_key="key", api_host="https://minimax.test")

    with pytest.raises(OcrResponseError):
        await client.extract_text(b"png")


async def test_ocr_image_builds_blocks_from_text() -> None:
    provider = MinimaxOcrProvider(
        FakeVisionClient("技术方案：20分\n资格要求：投标人须具备系统集成资质")
    )

    parsed, result = await ocr_document(provider, b"image-bytes", "scan.png")

    assert provider.client.calls == 1
    assert result.provider == "minimax"
    assert result.structure_used is False
    assert result.tables == []
    assert parsed.page_count == 1
    assert [block.text for block in parsed.blocks] == [
        "技术方案：20分",
        "资格要求：投标人须具备系统集成资质",
    ]
    assert all(block.page == 1 for block in parsed.blocks)


async def test_ocr_pdf_renders_pages_and_numbers_blocks() -> None:
    provider = MinimaxOcrProvider(FakeVisionClient("投标截止时间：2026年11月10日 09:00"))

    parsed, _result = await ocr_document(provider, _pdf_bytes(), "scan.pdf")

    assert provider.client.calls == 1
    assert parsed.page_count == 1
    assert parsed.blocks[0].page == 1


def test_render_pdf_pages_returns_png() -> None:
    images, total = render_pdf_pages(_pdf_bytes(), max_pages=5)

    assert total == 1
    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")
