import hashlib
import io
import math
import re
import struct
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from openpyxl import load_workbook
from pptx import Presentation
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import NotFoundError, ServiceUnavailableError, ValidationAppError
from app.modules.access.models import ResourceAcl
from app.modules.audit.service import AuditService
from app.modules.gateway.snapshots import build_snapshot_store
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.access import (
    CLEARANCE_RANK,
    accessible_kb_ids,
    can_access_document,
    can_access_kb,
    filter_documents,
    filter_images,
    raise_access_denied,
)
from app.modules.knowledge.image_sanitize import is_webp, webp_supported, webp_to_png
from app.modules.knowledge.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeImage,
)
from app.modules.tender.parser import (
    Block,
    ScannedDocumentError,
    UnsupportedDocumentError,
    parse_document,
)

DOCUMENT_SUFFIXES = {".docx", ".pdf", ".txt", ".md", ".xlsx", ".xlsm", ".pptx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CHUNK_CHARS = 600
CHUNK_OVERLAP = 80
MAX_KEYWORDS = 40
MAX_SCAN_CHUNKS = 5000

HEADING_PATTERN = re.compile(
    r"^(第[一二三四五六七八九十百]+[章节部分]|[一二三四五六七八九十]+、|\d+(?:\.\d+){0,3}\s+|#+\s+)"
)
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}|[0-9]{2,}")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
STOPWORDS = {
    "我们", "你们", "他们", "以及", "并且", "或者", "如果", "因为", "所以", "进行", "提供",
    "相关", "有关", "根据", "按照", "对于", "关于", "可以", "需要", "应当", "必须", "项目",
    "投标", "招标", "文件", "要求", "内容", "工作", "服务", "方案", "公司", "企业",
}


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    tokens = WORD_PATTERN.findall(lowered)
    for segment in CJK_PATTERN.findall(lowered):
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return [token for token in tokens if token not in STOPWORDS]


def _keywords(text: str, limit: int = MAX_KEYWORDS) -> list[str]:
    counts = Counter(_tokens(text))
    return [token for token, _ in counts.most_common(limit)]


def _chunks(text: str) -> list[tuple[str, str | None]]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    result: list[tuple[str, str | None]] = []
    buffer: list[str] = []
    current_heading: str | None = None
    size = 0
    for paragraph in paragraphs:
        if len(paragraph) <= 40 and HEADING_PATTERN.match(paragraph):
            current_heading = paragraph[:300]
        buffer.append(paragraph)
        size += len(paragraph)
        if size >= CHUNK_CHARS:
            chunk = "\n".join(buffer)
            result.append((chunk, current_heading))
            tail = chunk[-CHUNK_OVERLAP:]
            buffer = [tail] if tail else []
            size = len(tail)
    if buffer:
        chunk = "\n".join(buffer).strip()
        if chunk:
            result.append((chunk, current_heading))
    return result[:200]


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name:
        raise ValidationAppError("文件名不能为空")
    return name[:200]


def _image_dimensions(content: bytes) -> tuple[int | None, int | None]:
    if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
        width, height = struct.unpack(">II", content[16:24])
        return int(width), int(height)
    if content[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            marker = content[index + 1]
            if marker in {0xD8, 0xD9}:
                index += 2
                continue
            length = struct.unpack(">H", content[index + 2 : index + 4])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", content[index + 5 : index + 9])
                return int(width), int(height)
            index += 2 + length
    return None, None


def _extract_xlsx(content: bytes) -> str:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    lines: list[str] = []
    try:
        for sheet in workbook.worksheets:
            lines.append(f"工作表：{sheet.title}")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= 2000:
                    lines.append("（超出 2000 行已截断）")
                    break
                values = [str(value).strip() for value in row if value not in (None, "")]
                if values:
                    lines.append(" | ".join(values))
    finally:
        workbook.close()
    return "\n".join(lines)


def _extract_pptx(content: bytes) -> str:
    presentation = Presentation(io.BytesIO(content))
    lines: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines.append(f"幻灯片 {index}")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _block_text(blocks: list[Block]) -> str:
    lines: list[str] = []
    cells: list[str] = []
    current: tuple[int, int] | None = None
    for block in blocks:
        if block.kind == "table_cell" and block.table_index is not None and block.row is not None:
            key = (block.table_index, block.row)
            if current is not None and key != current:
                lines.append(" | ".join(cells))
                cells = []
            current = key
            cells.append(block.text)
        else:
            if cells:
                lines.append(" | ".join(cells))
                cells = []
            current = None
            lines.append(block.text)
    if cells:
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_document_text(filename: str, suffix: str, content: bytes) -> str:
    if suffix in {".xlsx", ".xlsm"}:
        try:
            text = _extract_xlsx(content)
        except Exception as exc:
            raise ValidationAppError("xlsx 文件解析失败") from exc
    elif suffix == ".pptx":
        try:
            text = _extract_pptx(content)
        except Exception as exc:
            raise ValidationAppError("pptx 文件解析失败") from exc
    else:
        text = _block_text(parse_document(content, filename).blocks)
    return text[:200_000]


async def _audit_break_glass_access(
    session: AsyncSession,
    user: CurrentUser,
    kb: KnowledgeBase,
    *,
    resource_type: str,
    resource_id: UUID,
) -> None:
    if kb.scope != "personal" or kb.owner_id == user.id:
        return
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.break_glass.access",
        resource_type=resource_type,
        resource_id=str(resource_id),
        data={"kb_id": str(kb.id), "owner_id": str(kb.owner_id) if kb.owner_id else None},
    )
    await session.commit()


async def _get_document_or_404(
    session: AsyncSession, tenant_id: UUID, document_id: UUID
) -> KnowledgeDocument:
    document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.id == document_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError()
    return document


async def _get_kb_or_404(session: AsyncSession, tenant_id: UUID, kb_id: UUID) -> KnowledgeBase:
    kb = (
        await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id == kb_id
            )
        )
    ).scalar_one_or_none()
    if kb is None:
        raise NotFoundError()
    return kb


async def ingest_document(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    kb_id: UUID,
    classification: str,
    company_id: UUID | None,
    title: str | None,
    kind: str,
    tags: list[str],
    filename: str,
    content_type: str | None,
    content: bytes,
) -> KnowledgeDocument:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValidationAppError("文件大小超过 20MB 上限")
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in DOCUMENT_SUFFIXES:
        raise ValidationAppError("仅支持 .docx / .pdf / .txt / .md / .xlsx / .pptx 文件")
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        text = extract_document_text(safe_name, suffix, content)
    except UnsupportedDocumentError as exc:
        raise ValidationAppError(str(exc)) from exc
    except ScannedDocumentError as exc:
        raise ValidationAppError("扫描版 PDF 需要先配置 OCR 服务") from exc
    try:
        store = build_snapshot_store(get_settings())
        object_ref = await store.put_bytes(
            f"knowledge/{tenant_id}/{file_hash[:12]}-{safe_name}",
            content,
            content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise ServiceUnavailableError("知识库存储不可用") from exc

    document = KnowledgeDocument(
        tenant_id=tenant_id,
        kb_id=kb_id,
        classification=classification,
        company_id=company_id,
        title=(title or safe_name)[:300],
        kind=kind[:30] or "other",
        filename=safe_name,
        content_type=content_type,
        size_bytes=len(content),
        file_hash=file_hash,
        object_ref=object_ref,
        status="parsed",
        text_content=text,
        tags=[tag.strip() for tag in tags if tag.strip()][:20],
        created_by=user_id,
    )
    session.add(document)
    await session.flush()
    for index, (chunk_text, heading) in enumerate(_chunks(text)):
        session.add(
            KnowledgeChunk(
                tenant_id=tenant_id,
                kb_id=kb_id,
                classification=classification,
                document_id=document.id,
                chunk_index=index,
                text=chunk_text,
                heading=heading,
                keywords=_keywords(chunk_text),
            )
        )
    await session.commit()
    await session.refresh(document)
    return document


async def ingest_image(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    kb_id: UUID,
    classification: str,
    company_id: UUID | None,
    title: str | None,
    caption: str | None,
    tags: list[str],
    sensitive: bool,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> KnowledgeImage:
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationAppError("图片大小超过 8MB 上限")
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValidationAppError("仅支持 .png / .jpg / .jpeg / .webp 图片")
    if suffix == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationAppError("文件不是有效的 png 图片")
        content_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        if not content.startswith(b"\xff\xd8\xff"):
            raise ValidationAppError("文件不是有效的 jpg 图片")
        content_type = "image/jpeg"
    if suffix == ".webp":
        if not is_webp(content):
            raise ValidationAppError("文件不是有效的 webp 图片")
        if not webp_supported():
            raise ServiceUnavailableError(
                "当前运行环境不支持 webp 转换，请上传 png 或 jpg"
            )
        try:
            content = webp_to_png(content)
        except Exception as exc:
            raise ValidationAppError("webp 图片无法解码") from exc
        if len(content) > MAX_IMAGE_BYTES:
            raise ValidationAppError("图片大小超过 8MB 上限")
        safe_name = f"{Path(safe_name).stem}.png"
        content_type = "image/png"
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        store = build_snapshot_store(get_settings())
        object_ref = await store.put_bytes(
            f"knowledge/{tenant_id}/images/{file_hash[:12]}-{safe_name}",
            content,
            content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise ServiceUnavailableError("知识库存储不可用") from exc
    width, height = _image_dimensions(content)
    image = KnowledgeImage(
        tenant_id=tenant_id,
        kb_id=kb_id,
        classification=classification,
        company_id=company_id,
        title=(title or safe_name)[:300],
        caption=caption,
        tags=[tag.strip() for tag in tags if tag.strip()][:20],
        filename=safe_name,
        content_type=content_type,
        size_bytes=len(content),
        file_hash=file_hash,
        object_ref=object_ref,
        width=width,
        height=height,
        sensitive=sensitive,
        created_by=user_id,
    )
    session.add(image)
    await session.commit()
    await session.refresh(image)
    return image


async def list_documents(
    session: AsyncSession, user: CurrentUser, kb_id: UUID | None = None
) -> list[KnowledgeDocument]:
    kb_ids = await accessible_kb_ids(session, user.tenant_id, user, "read")
    if not kb_ids:
        return []
    statement = select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == user.tenant_id,
        KnowledgeDocument.kb_id.in_(kb_ids),
    )
    if kb_id is not None:
        statement = statement.where(KnowledgeDocument.kb_id == kb_id)
    documents = list(
        (
            await session.execute(
                statement.order_by(KnowledgeDocument.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return await filter_documents(session, documents, user, "read")


async def list_images(
    session: AsyncSession, user: CurrentUser, kb_id: UUID | None = None
) -> list[KnowledgeImage]:
    kb_ids = await accessible_kb_ids(session, user.tenant_id, user, "read")
    if not kb_ids:
        return []
    statement = select(KnowledgeImage).where(
        KnowledgeImage.tenant_id == user.tenant_id,
        KnowledgeImage.kb_id.in_(kb_ids),
    )
    if kb_id is not None:
        statement = statement.where(KnowledgeImage.kb_id == kb_id)
    images = list(
        (
            await session.execute(
                statement.order_by(KnowledgeImage.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return await filter_images(session, images, user, "read")


async def delete_document(session: AsyncSession, user: CurrentUser, document_id: UUID) -> None:
    document = await _get_document_or_404(session, user.tenant_id, document_id)
    kb = await _get_kb_or_404(session, user.tenant_id, document.kb_id)
    if not await can_access_document(session, document, kb, user, "write"):
        raise_access_denied(kb, user)
    object_ref = document.object_ref
    await session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == user.tenant_id,
            KnowledgeChunk.document_id == document_id,
        )
    )
    await session.execute(
        delete(ResourceAcl).where(
            ResourceAcl.tenant_id == user.tenant_id,
            ResourceAcl.resource_type == "knowledge_document",
            ResourceAcl.resource_id == document_id,
        )
    )
    await session.delete(document)
    await session.commit()
    with suppress(Exception):
        await build_snapshot_store(get_settings()).delete_bytes(object_ref)


async def delete_image(session: AsyncSession, user: CurrentUser, image_id: UUID) -> None:
    image = (
        await session.execute(
            select(KnowledgeImage).where(
                KnowledgeImage.tenant_id == user.tenant_id, KnowledgeImage.id == image_id
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError()
    kb = await _get_kb_or_404(session, user.tenant_id, image.kb_id)
    if not await can_access_kb(session, kb, user, "write"):
        raise_access_denied(kb, user)
    object_ref = image.object_ref
    await session.delete(image)
    await session.commit()
    with suppress(Exception):
        await build_snapshot_store(get_settings()).delete_bytes(object_ref)


async def read_image_bytes(
    session: AsyncSession, user: CurrentUser, image_id: UUID
) -> tuple[KnowledgeImage, bytes]:
    image = (
        await session.execute(
            select(KnowledgeImage).where(
                KnowledgeImage.tenant_id == user.tenant_id, KnowledgeImage.id == image_id
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError()
    kb = await _get_kb_or_404(session, user.tenant_id, image.kb_id)
    if not await can_access_kb(session, kb, user, "read"):
        raise_access_denied(kb, user)
    if CLEARANCE_RANK.get(image.classification, 99) > CLEARANCE_RANK.get(user.clearance, 0):
        raise_access_denied(kb, user)
    await _audit_break_glass_access(
        session,
        user,
        kb,
        resource_type="knowledge_image",
        resource_id=image.id,
    )
    try:
        payload = await build_snapshot_store(get_settings()).get_bytes(image.object_ref)
    except Exception as exc:
        raise ServiceUnavailableError("知识库图片存储不可用") from exc
    return image, payload


def _score_chunks(
    query_tokens: list[str], rows: list[tuple[KnowledgeChunk, str, list[str]]]
) -> list[dict[str, Any]]:
    if not query_tokens:
        return []
    token_set = set(query_tokens)
    total = max(len(rows), 1)
    document_frequency: Counter[str] = Counter()
    for chunk, _, _ in rows:
        document_frequency.update(token_set & set(chunk.keywords or []))
    idf = {
        token: math.log((total + 1) / (document_frequency.get(token, 0) + 1)) + 1
        for token in token_set
    }
    scored: list[dict[str, Any]] = []
    for chunk, title, tags in rows:
        keywords = set(chunk.keywords or [])
        title_bonus = sum(idf[token] for token in token_set if token in title.lower())
        tag_text = " ".join(str(tag) for tag in (tags or [])).lower()
        tag_bonus = sum(idf[token] for token in token_set if token in tag_text)
        score = (
            sum(idf[token] for token in token_set if token in keywords)
            + title_bonus * 1.5
            + tag_bonus * 1.2
        )
        if score <= 0:
            continue
        scored.append(
            {
                "document_id": chunk.document_id,
                "title": title,
                "chunk": chunk.text,
                "heading": chunk.heading,
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def _select_hits(
    ranked: list[dict[str, Any]], limit: int, *, per_document: int = 2
) -> list[dict[str, Any]]:
    if not ranked:
        return []
    floor = ranked[0]["score"] * 0.2
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for hit in ranked:
        if hit["score"] < floor:
            break
        document_id = str(hit["document_id"])
        if counts[document_id] >= per_document:
            continue
        selected.append(hit)
        counts[document_id] += 1
        if len(selected) >= limit:
            break
    return selected


def section_query(
    *,
    title: str,
    scoring_text: str = "",
    project_name: str = "",
    buyer: str = "",
    code: str = "",
) -> str:
    parts = [title, scoring_text, project_name, buyer, code]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _score_images(
    query_tokens: list[str],
    images: list[KnowledgeImage],
    *,
    include_sensitive: bool,
    limit: int,
) -> list[dict[str, Any]]:
    token_set = set(query_tokens)
    if not token_set:
        return []
    scored: list[tuple[float, KnowledgeImage]] = []
    for image in images:
        if image.sensitive and not include_sensitive:
            continue
        title = image.title.lower()
        caption = (image.caption or "").lower()
        tags = " ".join(str(tag) for tag in (image.tags or [])).lower()
        score = (
            sum(1 for token in token_set if token in title) * 1.5
            + sum(1 for token in token_set if token in caption)
            + sum(1 for token in token_set if token in tags) * 1.2
        )
        if score > 0:
            scored.append((score, image))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    floor = scored[0][0] * 0.2
    results: list[dict[str, Any]] = []
    for score, image in scored:
        if score < floor:
            break
        results.append(
            {
                "id": image.id,
                "kb_id": getattr(image, "kb_id", None),
                "classification": getattr(image, "classification", "internal"),
                "title": image.title,
                "caption": image.caption,
                "tags": image.tags,
                "filename": image.filename,
                "content_type": image.content_type,
                "size_bytes": image.size_bytes,
                "width": image.width,
                "height": image.height,
                "sensitive": image.sensitive,
                "created_at": image.created_at,
                "score": round(score, 4),
            }
        )
        if len(results) >= limit:
            break
    return results


def _allowed_classifications(user: CurrentUser) -> list[str]:
    level = CLEARANCE_RANK.get(user.clearance, 0)
    return [name for name, rank in CLEARANCE_RANK.items() if rank <= level]


async def search(
    session: AsyncSession,
    user: CurrentUser,
    query: str,
    *,
    limit: int = 4,
    include_sensitive: bool = True,
    kb_id: UUID | None = None,
    action: str = "read",
) -> dict[str, list[dict[str, Any]]]:
    query_tokens = _tokens(query)
    kb_ids = await accessible_kb_ids(session, user.tenant_id, user, action)
    if not kb_ids:
        return {"chunks": [], "images": []}
    allowed = _allowed_classifications(user)
    statement = (
        select(KnowledgeChunk, KnowledgeDocument.title, KnowledgeDocument.tags)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeChunk.tenant_id == user.tenant_id,
            KnowledgeChunk.kb_id.in_(kb_ids),
            KnowledgeChunk.classification.in_(allowed),
            KnowledgeDocument.classification.in_(allowed),
        )
    )
    # kb_id 过滤必须在 limit 之前：否则先截断 5000 条再过滤，指定库的块可能
    # 已被其他库挤出窗口，表现为「库里明明有文档却检索不到」。
    if kb_id is not None:
        statement = statement.where(KnowledgeChunk.kb_id == kb_id)
    statement = statement.limit(MAX_SCAN_CHUNKS)
    rows = list((await session.execute(statement)).all())
    hits = _select_hits(
        _score_chunks(query_tokens, [(chunk, title, tags) for chunk, title, tags in rows]),
        limit,
    )
    document_ids = {hit["document_id"] for hit in hits}
    if document_ids:
        documents = list(
            (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.tenant_id == user.tenant_id,
                        KnowledgeDocument.id.in_(document_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        allowed_documents = {document.id for document in await filter_documents(session, documents, user, action)}
        hits = [hit for hit in hits if hit["document_id"] in allowed_documents]

    image_statement = select(KnowledgeImage).where(
        KnowledgeImage.tenant_id == user.tenant_id,
        KnowledgeImage.kb_id.in_(kb_ids),
        KnowledgeImage.classification.in_(allowed),
    )
    if kb_id is not None:
        image_statement = image_statement.where(KnowledgeImage.kb_id == kb_id)
    image_rows = list(
        (
            await session.execute(
                image_statement.order_by(KnowledgeImage.created_at.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    images = _score_images(
        query_tokens,
        image_rows,
        include_sensitive=include_sensitive,
        limit=limit,
    )
    return {"chunks": hits, "images": images}


async def knowledge_context(
    session: AsyncSession,
    user: CurrentUser,
    query: str,
    *,
    limit: int = 4,
    include_sensitive: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    return await search(
        session,
        user,
        query,
        limit=limit,
        include_sensitive=include_sensitive,
        action="use",
    )


def document_payload(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "kb_id": document.kb_id,
        "classification": document.classification,
        "title": document.title,
        "kind": document.kind,
        "filename": document.filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "status": document.status,
        "tags": document.tags or [],
        "error_message": document.error_message,
        "text_chars": len(document.text_content or ""),
        "created_at": document.created_at,
    }


def image_payload(image: KnowledgeImage) -> dict[str, Any]:
    return {
        "id": image.id,
        "kb_id": image.kb_id,
        "classification": image.classification,
        "title": image.title,
        "caption": image.caption,
        "tags": image.tags or [],
        "filename": image.filename,
        "content_type": image.content_type,
        "size_bytes": image.size_bytes,
        "width": image.width,
        "height": image.height,
        "sensitive": image.sensitive,
        "created_at": image.created_at,
    }


async def get_document_for_acl(
    session: AsyncSession, user: CurrentUser, document_id: UUID
) -> tuple[KnowledgeDocument, KnowledgeBase]:
    document = await _get_document_or_404(session, user.tenant_id, document_id)
    kb = await _get_kb_or_404(session, user.tenant_id, document.kb_id)
    return document, kb


async def get_document_detail(
    session: AsyncSession, user: CurrentUser, document_id: UUID
) -> tuple[KnowledgeDocument, list[KnowledgeChunk]]:
    document = await _get_document_or_404(session, user.tenant_id, document_id)
    kb = await _get_kb_or_404(session, user.tenant_id, document.kb_id)
    if not await can_access_document(session, document, kb, user, "read"):
        raise_access_denied(kb, user)
    await _audit_break_glass_access(
        session,
        user,
        kb,
        resource_type="knowledge_document",
        resource_id=document.id,
    )
    chunks = list(
        (
            await session.execute(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.tenant_id == user.tenant_id,
                    KnowledgeChunk.document_id == document_id,
                )
                .order_by(KnowledgeChunk.chunk_index)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return document, chunks


async def update_document(
    session: AsyncSession,
    user: CurrentUser,
    document_id: UUID,
    *,
    title: str | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
) -> KnowledgeDocument:
    document = await _get_document_or_404(session, user.tenant_id, document_id)
    kb = await _get_kb_or_404(session, user.tenant_id, document.kb_id)
    if not await can_access_document(session, document, kb, user, "write"):
        raise_access_denied(kb, user)
    if title is not None:
        document.title = title.strip()[:300] or document.title
    if kind is not None:
        document.kind = kind.strip()[:50] or document.kind
    if tags is not None:
        document.tags = [tag.strip() for tag in tags if tag.strip()][:20]
    await session.commit()
    await session.refresh(document)
    return document


async def update_image(
    session: AsyncSession,
    user: CurrentUser,
    image_id: UUID,
    *,
    title: str | None = None,
    caption: str | None = None,
    tags: list[str] | None = None,
    sensitive: bool | None = None,
) -> KnowledgeImage:
    image = (
        await session.execute(
            select(KnowledgeImage).where(
                KnowledgeImage.tenant_id == user.tenant_id, KnowledgeImage.id == image_id
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise NotFoundError()
    kb = await _get_kb_or_404(session, user.tenant_id, image.kb_id)
    if not await can_access_kb(session, kb, user, "write"):
        raise_access_denied(kb, user)
    if title is not None:
        image.title = title.strip() or image.title
    if caption is not None:
        image.caption = caption.strip() or None
    if tags is not None:
        image.tags = [tag.strip() for tag in tags if tag.strip()][:20]
    if sensitive is not None:
        image.sensitive = sensitive
    await session.commit()
    await session.refresh(image)
    return image
