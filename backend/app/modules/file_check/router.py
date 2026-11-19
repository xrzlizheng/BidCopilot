import contextlib
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.file_check.anbiao import load_tenant_identity, scan_document
from app.modules.file_check.rules import BUYER_RULES
from app.modules.file_check.semantic import (
    MAX_TOTAL_COMPARE_CHARS,
    FileCheckLimitError,
    combined_score,
    compare_prepared,
    prepare_document,
)
from app.modules.file_check.similarity import compare
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.tender.parser import UnsupportedDocumentError, parse_document

router = APIRouter(prefix="/api/v1/file-check", tags=["file-check"])

MAX_BYTES = 20 * 1024 * 1024
MAX_SIMILARITY_FILES = 10


async def _read(upload: UploadFile) -> tuple[str, bytes]:
    content = await upload.read()
    if len(content) > MAX_BYTES:
        raise ValidationAppError(f"{upload.filename}: 文件超过 20MB")
    return upload.filename or "document", content


def _parse(filename: str, content: bytes):
    try:
        parsed = parse_document(content, filename)
    except UnsupportedDocumentError as exc:
        raise ValidationAppError(str(exc)) from exc
    text = parsed.text.strip()
    if not text:
        raise ValidationAppError("未解析到文本；扫描件请先 OCR 或上传可复制文本版本")
    return parsed, text


@router.post("/tender")
async def check_tender_document(
    file: UploadFile = File(...),
    province: str | None = Form(default=None),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filename, content = await _read(file)
    parsed, text = _parse(filename, content)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for rule in BUYER_RULES:
        for block in parsed.blocks:
            for match in re.finditer(rule.pattern, block.text):
                key = (rule.code, block.index)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "rule_id": rule.code,
                        "level": rule.level,
                        "title": rule.title,
                        "suggestion": rule.suggestion,
                        "block_index": block.index,
                        "evidence": block.text[max(0, match.start() - 40) : match.end() + 40][:300],
                    }
                )
    findings.sort(key=lambda item: 0 if item["level"] == "high" else 1)
    high = sum(1 for item in findings if item["level"] == "high")
    medium = len(findings) - high
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="file_check.tender",
        resource_type="file",
        resource_id=filename,
        data={"province": province, "high": high, "medium": medium, "blocks": len(parsed.blocks)},
    )
    await session.commit()
    return {
        "status": "warning" if high else "ok",
        "filename": filename,
        "province": province,
        "summary": {
            "high": high,
            "medium": medium,
            "total": len(findings),
            "chars": len(text),
            "blocks": len(parsed.blocks),
        },
        "findings": findings,
        "disclaimer": "确定性规则提示，非法律意见；最终以招标文件与法规原文为准。",
    }


@router.post("/anbiao")
async def check_dark_bid_document(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """投标人侧暗标自查：上传技术标，检查身份泄露、自指表述、元数据与页眉页脚。"""
    from docx import Document as DocxDocument
    from pypdf import PdfReader

    from app.modules.tender.assembly import analyze_final_document

    filename, content = await _read(file)
    parsed, text = _parse(filename, content)
    suffix = Path(filename).suffix.lower()

    header_footer = ""
    if suffix == ".docx":
        document = DocxDocument(BytesIO(content))
        chunks: list[str] = []
        for section in document.sections:
            chunks.extend(paragraph.text for paragraph in section.header.paragraphs)
            chunks.extend(paragraph.text for paragraph in section.footer.paragraphs)
        header_footer = "\n".join(chunk for chunk in chunks if chunk.strip())
    metadata: dict[str, Any] = {}
    analysis = analyze_final_document(content, filename, project_name="")
    if analysis.get("analyzed"):
        metadata = dict(analysis.get("metadata") or {})
    elif suffix == ".pdf":
        with contextlib.suppress(Exception):
            metadata = dict(PdfReader(BytesIO(content)).metadata or {})

    identity = await load_tenant_identity(session, user.tenant_id)
    findings = scan_document(
        text=text,
        filename=filename,
        metadata=metadata,
        header_footer=header_footer,
        identity=identity,
    )
    findings.sort(key=lambda item: item["rule_id"])
    hard = len(findings)
    rules = sorted({item["rule_id"] for item in findings})
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="file_check.anbiao",
        resource_type="file",
        resource_id=filename,
        data={"findings": hard, "rules": rules, "chars": len(text)},
    )
    await session.commit()
    return {
        "status": "warning" if findings else "ok",
        "filename": filename,
        "summary": {
            "hard": hard,
            "rules": len(rules),
            "chars": len(text),
            "blocks": len(parsed.blocks),
            "metadata_checked": bool(metadata),
            "header_footer_checked": bool(suffix == ".docx"),
        },
        "findings": findings,
        "disclaimer": (
            "按暗标常见要求做确定性自查，不替代招标文件与评标委员会认定；"
            "最终以本项目招标文件的暗标条款为准。"
        ),
        "boundary": "只做合规提示与证据定位，不提供规避监管或规避雷同检测的能力。",
    }


@router.post("/similarity")
async def check_bid_similarity(
    files: list[UploadFile] = File(...),
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if len(files) < 2:
        raise ValidationAppError("至少上传 2 份文件")
    if len(files) > MAX_SIMILARITY_FILES:
        raise ValidationAppError(f"一次最多 {MAX_SIMILARITY_FILES} 份文件")
    parsed_files: list[tuple[str, str]] = []
    total_chars = 0
    for upload in files:
        filename, content = await _read(upload)
        _, text = _parse(filename, content)
        total_chars += len(text)
        parsed_files.append((filename, text))
    # 性能护栏：2–10 份文件两两比较是 O(n²)，先把总正文量钉住；
    # 超限报 400，不截断、不跳过——静默降级会让用户以为「已比对完」。
    if total_chars > MAX_TOTAL_COMPARE_CHARS:
        raise FileCheckLimitError(
            f"本次上传文件正文合计 {total_chars} 字，超过 {MAX_TOTAL_COMPARE_CHARS} 字上限；"
            "请减少文件份数或拆分正文后再自查（系统不会截断后给结论）。"
        )

    pairs: list[dict[str, Any]] = []
    # 语义画像只依赖单份文本，先各算一次复用，避免 45 对重复归一化。
    prepared = [(name, text, prepare_document(text)) for name, text in parsed_files]
    for left in range(len(prepared)):
        for right in range(left + 1, len(prepared)):
            lexical = compare(prepared[left][1], prepared[right][1])
            semantic = compare_prepared(prepared[left][2], prepared[right][2])
            pairs.append(
                {
                    "left": prepared[left][0],
                    "right": prepared[right][0],
                    # 既有字段原样保留（兼容）：score/level/common_excerpt 语义不变
                    **lexical,
                    # 新增字段
                    **semantic,
                    "combined_score": combined_score(
                        lexical["score"], semantic["semantic_score"]
                    ),
                }
            )
    # 排序改用字面+语义的确定性加权，旧的 score 仍原样返回；
    # 两级排序键保证分数相同时输出顺序也稳定（不依赖列表顺序之外的随机因素）。
    pairs.sort(key=lambda item: (-item["combined_score"], item["left"], item["right"]))
    high = sum(1 for item in pairs if item["level"] == "high")
    medium = sum(1 for item in pairs if item["level"] == "medium")
    semantic_high = sum(1 for item in pairs if item["semantic_level"] == "high")
    semantic_medium = sum(1 for item in pairs if item["semantic_level"] == "medium")
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="file_check.similarity",
        resource_type="file",
        resource_id=f"{len(prepared)} files",
        data={
            "high": high,
            "medium": medium,
            "semantic_high": semantic_high,
            "semantic_medium": semantic_medium,
            "pairs": len(pairs),
            "chars": total_chars,
        },
    )
    await session.commit()
    return {
        "status": "warning" if high or semantic_high else "ok",
        "files": [name for name, _, _ in prepared],
        "summary": {
            "files": len(prepared),
            "pairs": len(pairs),
            "high": high,
            "medium": medium,
            "semantic_high": semantic_high,
            "semantic_medium": semantic_medium,
            "chars": total_chars,
        },
        "pairs": pairs,
        "disclaimer": "仅用于跨投标人雷同/合规自查，不提供规避检测建议；结果仅供人工复核。",
        "boundary": "相似度只提示「需要复核」，不构成「串标」认定，也不是法律意见。",
    }
