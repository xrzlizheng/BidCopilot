import asyncio
import base64
import io
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.modules.tender.parser import Block, ParsedDocument, UnsupportedDocumentError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
# MinerU 4 官方支持的输入类型全集（PDF/OFD/EPUB/HTML/MHTML/图片/CSV/RTF/
# DOC/DOCX/PPT/PPTX/XLS/XLSX/ODT/ODS/ODP）。本地 OCR 通道据此放宽白名单，
# 未收录后缀 MinerU 会自行报错并经 OcrResponseError 诚实透出。
MINERU_SUFFIXES = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".gif",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
        ".rtf",
        ".odt",
        ".ods",
        ".odp",
        ".epub",
        ".html",
        ".mhtml",
        ".ofd",
    }
)
OCR_PROMPT = (
    "你是一份招标文件的 OCR 引擎。请提取图片中的全部文字，保持原有顺序、编号、标题层级、"
    "表格行列与标点；不要总结、不要解释、不要补写原文没有的内容。"
)


class OcrError(Exception):
    pass


class OcrConfigError(OcrError):
    pass


class OcrTransportError(OcrError):
    pass


class OcrAuthError(OcrError):
    pass


class OcrQuotaError(OcrError):
    pass


class OcrResponseError(OcrError):
    pass


class OcrModelMissingError(OcrError):
    pass


@dataclass
class OcrPage:
    page_no: int
    text: str


@dataclass
class OcrTable:
    page: int
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class OcrResult:
    pages: list[OcrPage]
    tables: list[OcrTable]
    provider: str
    structure_used: bool


class OcrProvider(Protocol):
    name: str

    async def recognize(
        self, content: bytes, filename: str, *, max_pages: int
    ) -> OcrResult: ...


class MiniMaxVisionClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_host: str | None,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_host = api_host
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._keys = [key.strip() for key in (api_key or "").split(",") if key.strip()]
        self._key_index = 0

    def configured(self) -> bool:
        return bool(self.api_host and self._keys)

    def _next_key(self) -> str:
        if not self._keys:
            raise OcrConfigError("MINIMAX_API_KEY is not configured")
        key = self._keys[self._key_index % len(self._keys)]
        self._key_index += 1
        return key

    async def extract_text(self, image: bytes, *, mime_type: str = "image/png") -> str:
        if not self.api_host:
            raise OcrConfigError("MINIMAX_API_HOST is not configured")
        if not image:
            raise OcrResponseError("image is empty")
        encoded = base64.b64encode(image).decode()
        payload: dict[str, Any] = {
            "prompt": OCR_PROMPT,
            "image_url": f"data:{mime_type};base64,{encoded}",
        }
        headers = {
            "Authorization": f"Bearer {self._next_key()}",
            "MM-API-Source": "Minimax-MCP",
            "Content-Type": "application/json",
        }
        url = f"{self.api_host.rstrip('/')}/v1/coding_plan/vlm"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, trust_env=False
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OcrTransportError(f"MiniMax vision request failed: {exc}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise OcrResponseError("MiniMax vision returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise OcrResponseError("MiniMax vision returned a non-object payload")
        base = body.get("base_resp") or {}
        code = base.get("status_code", 0)
        if code != 0:
            message = str(base.get("status_msg") or "unknown error")
            if code == 1004:
                raise OcrAuthError(f"MiniMax vision auth failed: {message}")
            if code == 1008:
                raise OcrQuotaError(f"MiniMax vision quota exhausted: {message}")
            raise OcrResponseError(f"MiniMax vision error {code}: {message}")
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OcrResponseError("MiniMax vision returned empty content")
        return content.strip()


class MinimaxOcrProvider:
    """云端视觉 OCR：返回整页文本，无结构化表格（table_cells 恒空）。"""

    name = "minimax"

    def __init__(self, client: MiniMaxVisionClient) -> None:
        self.client = client

    def configured(self) -> bool:
        return self.client.configured()

    async def recognize(
        self, content: bytes, filename: str, *, max_pages: int
    ) -> OcrResult:
        suffix = Path(filename).suffix.lower()
        pages: list[OcrPage] = []
        if suffix == ".pdf":
            images, total = render_pdf_pages(content, max_pages=max_pages)
            for index, image in enumerate(images, start=1):
                text = await self.client.extract_text(image, mime_type="image/png")
                pages.append(OcrPage(page_no=index, text=text))
        elif suffix in IMAGE_SUFFIXES:
            text = await self.client.extract_text(
                content, mime_type=MIME_BY_SUFFIX[suffix]
            )
            pages.append(OcrPage(page_no=1, text=text))
        else:
            raise UnsupportedDocumentError(f"ocr does not support: {suffix}")
        return OcrResult(
            pages=pages, tables=[], provider=self.name, structure_used=False
        )


class _TableHTMLParser(HTMLParser):
    """MinerU 表格体输出 HTML；这里解析成行列文本，容忍 <br>/<b> 等内联标签。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self._row.append(text)  # type: ignore[union-attr]
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _html_table_to_rows(html: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    return parser.rows


def _extract_items(result: Any) -> list[dict[str, Any]]:
    """ParseResult → content_list V1 扁平条目。

    条目形如 {"type": "text", "text": ..., "page_idx": 0} 与
    {"type": "table", "table_body": "<table>...", "page_idx": 1}；
    图片条目仅含 img_path、无 text 字段，映射时自然跳过。
    """
    # mineru 未提供 py.typed，仅本地 OCR 可选依赖装配时才会走到这里
    from mineru.render.content_list import render_content_list  # type: ignore[import-untyped]

    return render_content_list(result.middle_json)


def _items_to_ocr_result(items: list[Any], provider_name: str) -> OcrResult:
    """content_list 条目 → OcrResult：文本条目按 page_idx 归页，表格体 HTML 解析成行列。"""
    page_lines: dict[int, list[str]] = {}
    tables: list[OcrTable] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        page_no = int(item.get("page_idx") or 0) + 1
        item_type = str(item.get("type") or "")
        if item_type == "table":
            page_lines.setdefault(page_no, [])
            html = item.get("table_body")
            if isinstance(html, str) and html.strip():
                rows = _html_table_to_rows(html)
                if rows:
                    tables.append(OcrTable(page=page_no, rows=rows))
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            page_lines.setdefault(page_no, []).append(text.strip())
    pages = [
        OcrPage(page_no=page_no, text="\n".join(lines))
        for page_no, lines in sorted(page_lines.items())
    ]
    return OcrResult(
        pages=pages, tables=tables, provider=provider_name, structure_used=bool(tables)
    )


# MinerU 模型常驻内存且 CPU 推理非线程安全，模块级锁串行化解析，防并发内存翻倍。
_MINERU_PARSE_LOCK = asyncio.Lock()


class MineruOcrProvider:
    """本地 OCR：MinerU（tier 见 resolve_mineru_tier），结构化文本 + 表格。

    parser 惰性构建并缓存；mineru 未安装时可用性由工厂经 mineru_available()
    判定，运行中缺失则报 OcrModelMissingError。输入 bytes 落临时文件后
    调 parse_async（内部自转线程池，不阻塞事件循环），PDF 经 page_range
    截取页数预算；解析异常一律转 OcrResponseError 诚实上报，不降级。
    tier=auto 的 GPU 探测延迟到首次 recognize（async 上下文 + to_thread），
    避免 torch 导入阻塞事件循环；结果缓存在实例上。
    """

    name = "mineru"

    def __init__(
        self,
        *,
        tier: str = "auto",
        min_vram_gb: float = 8.0,
        timeout_seconds: float = 300.0,
        parser_factory: Any | None = None,
    ) -> None:
        self.tier = tier
        self.min_vram_gb = min_vram_gb
        self.effective_tier: str | None = None
        self.timeout_seconds = timeout_seconds
        self._parser_factory = parser_factory
        self._parser: Any | None = None

    async def _resolve_tier(self) -> str:
        """解析生效档位：显式 tier 直通；auto 按显存自适应，结果缓存。"""
        if self.effective_tier is not None:
            return self.effective_tier
        if self.tier != "auto":
            self.effective_tier = self.tier
            return self.effective_tier
        vram = await asyncio.to_thread(detect_gpu_vram_gb)
        self.effective_tier = (
            "standard" if vram is not None and vram >= self.min_vram_gb else "basic"
        )
        return self.effective_tier

    def _get_parser(self, tier: str) -> Any:
        if self._parser is None:
            try:
                if self._parser_factory is not None:
                    self._parser = self._parser_factory()
                else:
                    # mineru 未提供 py.typed，按 Any 处理其构造参数
                    from mineru.parser import MinerUParser  # type: ignore[import-untyped]

                    self._parser = MinerUParser(
                        tier=tier,
                        # OCR 入口只处理无文字层的扫描件/图片，强制 OCR 模式；
                        # 图片理解（caption 生成）对约束抽取无用，关闭省 CPU。
                        parse_mode="ocr",
                        image_analysis=False,
                    )
            except ImportError as exc:
                raise OcrModelMissingError("mineru runtime not available") from exc
        return self._parser

    async def recognize(
        self, content: bytes, filename: str, *, max_pages: int
    ) -> OcrResult:
        suffix = Path(filename).suffix.lower()
        if suffix not in MINERU_SUFFIXES:
            raise UnsupportedDocumentError(f"ocr does not support: {suffix}")
        if suffix in IMAGE_SUFFIXES and not content:
            raise OcrResponseError("image is empty")

        tier = await self._resolve_tier()
        parser = self._get_parser(tier)
        page_range = f"1-{max_pages}" if suffix == ".pdf" else ""
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(content)
                tmp_path = handle.name
            async with _MINERU_PARSE_LOCK:
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        result = await parser.parse_async(tmp_path, page_range=page_range)
                except TimeoutError as exc:
                    raise OcrTransportError(
                        f"mineru OCR timed out after {self.timeout_seconds}s"
                    ) from exc
                except OcrError:
                    raise
                except Exception as exc:
                    raise OcrResponseError(f"mineru OCR failed: {exc}") from exc
        finally:
            if tmp_path is not None:
                Path(tmp_path).unlink(missing_ok=True)
        return _items_to_ocr_result(_extract_items(result), self.name)


def mineru_available() -> bool:
    """探测本地 OCR 依赖是否安装；find_spec 只查存在性，不触发重型导入。"""
    return find_spec("mineru") is not None


@lru_cache(maxsize=1)
def detect_gpu_vram_gb() -> float | None:
    """探测 CUDA GPU 总显存（GB），无 CUDA 运行时（含 torch-cpu 构建）返回 None。

    结果进程级缓存：torch 导入开销数秒，不随请求重复支付。
    """
    try:
        import torch
    except Exception:  # ImportError 或导入期炸（缺 libcuda 等），一律按无 GPU 处理
        return None
    try:
        if not torch.cuda.is_available():
            return None
        return float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
    except Exception:
        return None


def resolve_mineru_tier(tier: str, *, min_vram_gb: float) -> str:
    """MINERU_TIER=auto 时按显存自适应选档：足量 → standard（VLM），否则 basic。

    显式 tier（flash/basic/standard/advanced）原样尊重——那是部署方指定类型的入口。
    注意 tier 的档位选择同时就是 GPU 的使用决策：standard/advanced 走 VLM 引擎
    （CUDA 构建镜像下用 GPU），basic 走 ONNX 小模型（纯 CPU）。
    """
    tier = (tier or "auto").strip().lower()
    if tier != "auto":
        return tier
    vram = detect_gpu_vram_gb()
    if vram is not None and vram >= min_vram_gb:
        return "standard"
    return "basic"


def build_ocr_provider(settings: Any) -> OcrProvider | None:
    """OCR 供应商工厂。

    auto：配了 MiniMax 用云端，否则本地 mineru 可用则用本地，都无 → None。
    显式选择不可用时同样返回 None——误配置经 /system/ai-status 的 ocr 字段
    诚实透出，不在依赖构建时抛错（上传路径会以「OCR 服务未配置」失败）。
    """
    choice = (getattr(settings, "OCR_PROVIDER", "auto") or "auto").strip().lower()
    if choice == "none":
        return None
    minimax = MinimaxOcrProvider(
        MiniMaxVisionClient(
            api_key=settings.MINIMAX_API_KEY,
            api_host=settings.MINIMAX_API_HOST,
            timeout_seconds=getattr(
                settings, "OCR_TIMEOUT_SECONDS", settings.MINIMAX_OCR_TIMEOUT_SECONDS
            ),
        )
    )
    if choice == "minimax":
        return minimax if minimax.configured() else None
    mineru: MineruOcrProvider | None = None
    if (choice == "mineru" or (choice == "auto" and not minimax.configured())) and mineru_available():
        mineru = MineruOcrProvider(
            tier=str(getattr(settings, "MINERU_TIER", "auto") or "auto"),
            min_vram_gb=float(getattr(settings, "MINERU_GPU_MIN_VRAM_GB", 8.0)),
            timeout_seconds=float(getattr(settings, "MINERU_TIMEOUT_SECONDS", 300.0)),
        )
    if choice == "mineru":
        return mineru
    if choice == "auto":
        return minimax if minimax.configured() else mineru
    return None


def render_pdf_pages(content: bytes, *, max_pages: int) -> tuple[list[bytes], int]:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    document = pdfium.PdfDocument(content)
    try:
        total = len(document)
        images: list[bytes] = []
        for index in range(min(total, max_pages)):
            page = document[index]
            bitmap = page.render(scale=2.0)
            image = bitmap.to_pil()
            try:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                images.append(buffer.getvalue())
            finally:
                image.close()
        return images, total
    finally:
        document.close()


def parsed_from_ocr(result: OcrResult) -> ParsedDocument:
    """OCR 结果 → ParsedDocument：文本行作段落块，结构化表格作 table_cell 块，
    与 docx 解析同形，下游表格优先抽取逻辑直接复用。"""
    blocks: list[Block] = []
    for page in result.pages:
        for line in page.text.splitlines():
            text = line.strip()
            if text:
                blocks.append(
                    Block(
                        index=len(blocks),
                        text=text,
                        kind="paragraph",
                        page=page.page_no,
                    )
                )
    for table_index, table in enumerate(result.tables):
        for row_no, row in enumerate(table.rows):
            for col_no, cell in enumerate(row):
                text = (cell or "").strip()
                if not text:
                    continue
                blocks.append(
                    Block(
                        index=len(blocks),
                        text=text,
                        kind="table_cell",
                        page=table.page,
                        table_index=table_index,
                        row=row_no,
                        col=col_no,
                        header=(row_no == 0),
                    )
                )
    if not blocks:
        raise OcrResponseError("OCR returned no text")
    return ParsedDocument(blocks=blocks, page_count=len(result.pages))


async def ocr_document(
    provider: OcrProvider,
    content: bytes,
    filename: str,
    *,
    max_pages: int = 10,
) -> tuple[ParsedDocument, OcrResult]:
    result = await provider.recognize(content, filename, max_pages=max_pages)
    return parsed_from_ocr(result), result
