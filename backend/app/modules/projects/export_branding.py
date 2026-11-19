"""导出内嵌交付品牌：解析「以谁的名义交付」，并保证 Logo 缺失时导出不失败。

口径（与交付摘要页保持一致）：
- 产品面向企业投标部门，交付主体就是投标企业自身：品牌取 ``project.company_id``
  对应企业的名称；项目未关联企业时回退为 ``BidCopilot``。
- 不做白标：企业以自己的名义投标，不需要「弱化工具品牌」这类代理机构诉求。
- Logo 仅接受对象存储（snapshot store）引用：``s3://...`` 或 local store 的对象键/绝对
  路径。``http(s)://`` 等外部地址不主动抓取——外部数据只允许经 ``app/modules/gateway``
  获取，因此这里显式跳过并在日志中说明，不允许退化成一次无网关约束的下载。
- 任何 Logo 读取/解码/嵌入失败都只降级为「仅品牌名」：Logo 是装饰性资产，不能让它把
  导出打成 500。降级事实通过 ``ExportBranding.has_logo`` 暴露给审计，不掩盖。
"""

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.spreadsheet import excel_row
from app.db.repository import TenantRepository
from app.modules.assets.models import Company
from app.modules.gateway.snapshots import SnapshotStore, build_snapshot_store
from app.modules.knowledge.image_sanitize import (
    is_webp,
    strip_image_metadata,
    webp_to_png,
)
from app.modules.projects.service import get_project

logger = logging.getLogger("bidcopilot.export_branding")


class _CompanyRepository(TenantRepository[Company]):
    model = Company


FALLBACK_BRAND_NAME = "BidCopilot"
BRAND_ROW_LABEL = "交付品牌"
# 客户 Logo 不属于招标正文，4MB 上限足够且避免把超大图塞进导出文件。
MAX_LOGO_BYTES = 4 * 1024 * 1024
# xlsx 中 Logo 的展示尺寸上限（像素）；按原始宽高比等比缩放。
MAX_XLSX_LOGO_WIDTH_PX = 160
MAX_XLSX_LOGO_HEIGHT_PX = 60
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


@dataclass(frozen=True)
class ExportBranding:
    """一次导出所代表的品牌。Logo 允许缺失。"""

    name: str
    logo_bytes: bytes | None = None
    logo_content_type: str | None = None
    client_name: str | None = None
    # 投标企业 Logo 的原始引用（对象存储 key 或 s3:// 地址）。交付摘要页用它渲染 Logo，
    # 导出侧用上面的 `logo_bytes` 内嵌；两者同源于这一次解析，避免
    # 「交付页显示的牌子」与「导出文件里的牌子」不一致。
    logo_url: str | None = None

    @property
    def has_logo(self) -> bool:
        return self.logo_bytes is not None

    def audit_fields(self, *, logo_embedded: bool | None = None) -> dict[str, Any]:
        """审计用字段：记录「以谁的名义交付」以及 Logo 是否随导出提供。

        ``brand_logo`` 表示本次导出是否带有可用 Logo 字节；具体渲染环节若失败会记
        warning 日志（不静默），审计只如实记录「提供了/没提供」。
        """
        embedded = self.has_logo if logo_embedded is None else logo_embedded
        return {
            "brand": self.name,
            "brand_logo": embedded,
            "brand_client": self.client_name,
        }


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


async def _resolve_company_for_project(
    session: AsyncSession, tenant_id: UUID, project: Any
) -> Company | None:
    """交付主体就是投标企业自身：按 ``project.company_id`` 取本租户企业。

    读取经 ``TenantRepository.scoped`` 加租户过滤；查不到即视为未指定投标主体
    （不是 404：项目本身已由 ``get_project`` 校验）。
    """
    if project.company_id is None:
        return None
    return (
        await session.execute(
            _CompanyRepository(session, tenant_id).scoped(
                select(Company).where(Company.id == project.company_id)
            )
        )
    ).scalar_one_or_none()


async def resolve_export_branding(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    store: SnapshotStore | None = None,
) -> ExportBranding:
    """解析导出品牌。任何 Logo 问题都降级，不抛异常（品牌名始终可用）。"""
    project = await get_project(session, tenant_id, project_id)
    return await branding_for_project(session, tenant_id, project, store=store)


async def branding_for_project(
    session: AsyncSession,
    tenant_id: UUID,
    project: Any,
    *,
    store: SnapshotStore | None = None,
) -> ExportBranding:
    """已持有 project 对象时的品牌解析（避免重复查询与重复访问校验）。"""
    company = await _resolve_company_for_project(session, tenant_id, project)
    if company is None:
        return ExportBranding(name=FALLBACK_BRAND_NAME)
    name = _clean(company.name) or FALLBACK_BRAND_NAME
    logo_ref = None
    logo_bytes, logo_content_type = await load_brand_logo(logo_ref, store=store)
    return ExportBranding(
        name=name,
        logo_bytes=logo_bytes,
        logo_content_type=logo_content_type,
        logo_url=logo_ref,
        client_name=_clean(company.name),
    )


async def load_brand_logo(
    ref: str | None, *, store: SnapshotStore | None = None
) -> tuple[bytes | None, str | None]:
    """从对象存储读取客户 Logo 字节；失败返回 ``(None, None)``。

    返回的字节已剥离图片元数据并归一为 PNG/JPEG（WebP 转 PNG），保证 openpyxl 与
    python-docx 都能嵌入。
    """
    cleaned = _clean(ref)
    if cleaned is None:
        return None, None
    scheme = cleaned.split(":", 1)[0].lower()
    if "://" in cleaned and not cleaned.startswith("s3://"):
        # 外部 URL 需要经过网关与地址策略，这里不做无约束抓取，直接降级。
        logger.info("客户 Logo 引用非对象存储地址（scheme=%s），跳过嵌入", scheme)
        return None, None
    try:
        snapshot_store = store if store is not None else build_snapshot_store(get_settings())
        payload = await snapshot_store.get_bytes(cleaned)
    except Exception as exc:  # noqa: BLE001 - Logo 缺失不能影响导出
        logger.warning("客户 Logo 读取失败，已降级为仅品牌名：%s", type(exc).__name__)
        return None, None
    if not payload:
        logger.warning("客户 Logo 内容为空，跳过嵌入")
        return None, None
    if len(payload) > MAX_LOGO_BYTES:
        logger.warning("客户 Logo 超过 %s 字节上限，跳过嵌入", MAX_LOGO_BYTES)
        return None, None
    return _normalise_image(payload)


def _normalise_image(payload: bytes) -> tuple[bytes | None, str | None]:
    if payload.startswith(PNG_SIGNATURE):
        return strip_image_metadata(payload, "image/png"), "image/png"
    if payload.startswith(JPEG_SIGNATURE):
        return strip_image_metadata(payload, "image/jpeg"), "image/jpeg"
    if is_webp(payload):
        try:
            return webp_to_png(payload), "image/png"
        except Exception as exc:  # noqa: BLE001 - 运行时不支持 webp 时降级
            logger.warning("客户 Logo（webp）转换失败，跳过嵌入：%s", type(exc).__name__)
            return None, None
    logger.warning("客户 Logo 不是受支持的图片格式，跳过嵌入")
    return None, None


def branding_for_export(
    branding: ExportBranding | None, *, dark_bid: bool
) -> ExportBranding | None:
    """按导出形态决定是否内嵌品牌。

    暗标（dark_bid）导出必须不出现任何身份信息——客户品牌同样是身份信息，因此一律
    不嵌入；该判断集中在此，避免各导出路径各写一份。
    """
    return None if dark_bid else branding


def append_branding_row(
    sheet: Any,
    branding: ExportBranding | None,
    *,
    logo_anchor: str | None = None,
) -> bool:
    """在 Summary 表顶部追加「交付品牌」行，若 Logo 可得则同时嵌入。返回是否嵌入 Logo。

    ``branding=None`` 时不做任何改动（既有导出行为与列布局保持不变）。Logo 锚点默认
    取品牌行右侧空白单元格，并把该行行高撑到能容纳 Logo，避免压住下方「项目」行。
    """
    if branding is None or not branding.name:
        return False
    sheet.append(excel_row([BRAND_ROW_LABEL, branding.name]))
    if not branding.logo_bytes:
        return False
    anchor = logo_anchor or f"D{sheet.max_row}"
    return _embed_logo_in_sheet(sheet, branding, anchor, row_index=sheet.max_row)


def _embed_logo_in_sheet(
    sheet: Any, branding: ExportBranding, anchor: str, *, row_index: int | None = None
) -> bool:
    try:
        from openpyxl.drawing.image import Image as WorksheetImage

        logo_bytes = branding.logo_bytes
        if not logo_bytes:
            return False
        image = WorksheetImage(BytesIO(logo_bytes))
        width = int(getattr(image, "width", 0) or 0)
        height = int(getattr(image, "height", 0) or 0)
        if width > 0 and height > 0:
            scale = min(
                MAX_XLSX_LOGO_WIDTH_PX / width,
                MAX_XLSX_LOGO_HEIGHT_PX / height,
                1.0,
            )
            image.width = max(1, int(width * scale))
            image.height = max(1, int(height * scale))
        if anchor:
            sheet.add_image(image, anchor)
        else:
            sheet.add_image(image)
        if row_index is not None:
            # 1 px ≈ 0.75 pt；留 4pt 余量，避免 Logo 压到下一行文字。
            needed = float(getattr(image, "height", 0) or 0) * 0.75 + 4
            current = sheet.row_dimensions[row_index].height or 0
            if needed > current:
                sheet.row_dimensions[row_index].height = needed
        return True
    except Exception as exc:  # noqa: BLE001 - Logo 嵌入失败不能影响导出
        logger.warning("xlsx 客户 Logo 嵌入失败，已降级为仅品牌名：%s", type(exc).__name__)
        return False


def apply_docx_branding(document: Any, branding: ExportBranding | None) -> bool:
    """在 docx 页眉写入品牌名（及 Logo）。失败只降级，不抛异常。

    注意：暗标（dark_bid）导出禁止出现任何身份信息，调用方必须先跳过品牌注入；
    这里不做暗标判断，避免把「是否匿名」的决策藏进展示函数。
    """
    if branding is None or not branding.name:
        return False
    try:
        section = document.sections[0] if document.sections else None
        if section is None:
            return False
        header = section.header
        paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        if branding.name in paragraph.text:
            # 模板/调用方已写过同一品牌，避免重复。
            return False
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if branding.logo_bytes:
            try:
                paragraph.add_run().add_picture(BytesIO(branding.logo_bytes), width=Cm(3.0))
            except Exception as exc:  # noqa: BLE001 - Logo 嵌入失败不能影响导出
                logger.warning(
                    "docx 页眉客户 Logo 嵌入失败，已降级为仅品牌名：%s", type(exc).__name__
                )
        run = paragraph.add_run(branding.name)
        run.bold = True
        return True
    except Exception as exc:  # noqa: BLE001 - 品牌注入失败不能影响导出
        logger.warning("docx 页眉品牌写入失败，已降级为不嵌入：%s", type(exc).__name__)
        return False


def branding_pdf_lines(branding: ExportBranding | None) -> list[str]:
    """PDF 报告的品牌说明行（调用方负责转义与字体）。"""
    if branding is None or not branding.name:
        return []
    return [f"交付品牌：{branding.name}"]
