from io import BytesIO
from typing import Any
from uuid import UUID

from docx import Document as DocxDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.generation.models import BidSection
from app.modules.projects.commitments import list_commitments
from app.modules.projects.models import BidDecisionRun, ScoringItem
from app.modules.projects.service import get_latest_constraint, get_project


async def build_defense_outline(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    minutes: int = 20,
    include_amounts: bool = False,
) -> dict[str, Any]:
    project = await get_project(session, tenant_id, project_id)
    constraint = await get_latest_constraint(session, tenant_id, project_id)
    payload = constraint.payload if constraint else {}
    scoring_items = list(
        (
            await session.execute(
                select(ScoringItem)
                .where(
                    ScoringItem.tenant_id == tenant_id,
                    ScoringItem.project_id == project_id,
                    # constraint 缺失时该查询根本不会执行（外层三元）；
                    # 用条件解包避免把 None 传进 where（SQLAlchemy 会抛 ArgumentError）。
                    *([ScoringItem.constraint_id == constraint.id] if constraint else []),
                )
                .order_by(ScoringItem.created_at)
            )
        ).scalars()
    ) if constraint else []
    sections = list(
        (
            await session.execute(
                select(BidSection)
                .where(BidSection.tenant_id == tenant_id, BidSection.project_id == project_id)
                .order_by(BidSection.order_index)
            )
        ).scalars()
    )
    commitments = await list_commitments(session, tenant_id, project_id)
    decision = (
        await session.execute(
            select(BidDecisionRun)
            .where(
                BidDecisionRun.tenant_id == tenant_id,
                BidDecisionRun.project_id == project_id,
                BidDecisionRun.engine_mode == "full",
            )
            .order_by(BidDecisionRun.run_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    sections_by_scoring = {
        str(section.scoring_item_id): section for section in sections if section.scoring_item_id
    }
    slides: list[dict[str, Any]] = [
        {
            "title": "开场与项目理解",
            "minutes": 2,
            "points": [
                f"项目：{project.name}",
                f"采购人：{project.buyer or '未披露'}",
                "用一句话说明我们理解的项目目标与成功标准。",
            ],
            "source": "project",
        }
    ]
    substantive = payload.get("substantive_requirements") or []
    if substantive:
        slides.append(
            {
                "title": "需求与难点",
                "minutes": 2,
                "points": [
                    str(item.get("text") or "")[:160]
                    for item in substantive[:4]
                    if isinstance(item, dict)
                ],
                "source": "tender.substantive_requirements",
            }
        )
    total_score = sum(float(item.score or 0) for item in scoring_items) or 1.0
    for item in scoring_items:
        section = sections_by_scoring.get(str(item.id))
        weight = max(1, round(minutes * float(item.score or 0) / total_score))
        points = [f"评分项：{item.item}（{item.score or '—'} 分）"]
        if item.criteria:
            points.append(f"评分标准：{item.criteria[:160]}")
        if section is not None:
            points.append(f"对应章节：{section.title}")
            if section.content_text:
                points.append(section.content_text.strip().splitlines()[0][:160])
        slides.append(
            {
                "title": f"评分响应：{item.item[:40]}",
                "minutes": weight,
                "points": points,
                "source": "scoring_item",
            }
        )
    commitment_points: list[str] = []
    for commitment in commitments:
        if commitment.kind in {"schedule", "warranty", "personnel"}:
            commitment_points.append(f"{commitment.label}：{commitment.value_text}")
    if commitment_points:
        slides.append(
            {
                "title": "实施与服务承诺",
                "minutes": 2,
                "points": commitment_points[:6],
                "source": "bid_commitments",
            }
        )
    if include_amounts and decision is not None and (decision.inputs_json or {}).get("quote_amount"):
        quote_amount = (decision.inputs_json or {})["quote_amount"]
        slides.append(
            {
                "title": "商务与报价口径",
                "minutes": 2,
                "points": [
                    f"投标报价：{quote_amount} 元",
                    "说明报价构成与成本控制措施，不承诺中标。",
                ],
                "source": "decision_card",
            }
        )
    slides.append(
        {
            "title": "结尾与承诺",
            "minutes": 1,
            "points": [
                "重申关键承诺与交付保障。",
                "感谢评委，留出问答时间。",
            ],
            "source": "template",
        }
    )
    allocated = sum(int(slide["minutes"]) for slide in slides)
    return {
        "project": {"id": str(project.id), "name": project.name, "buyer": project.buyer},
        "minutes": minutes,
        "allocated_minutes": allocated,
        "slides": slides,
        "sources": {
            "scoring_items": len(scoring_items),
            "sections": len(sections),
            "commitments": len(commitments),
            "decision_run": decision.run_no if decision else None,
        },
        "notes": ["提纲为结构化草稿，需人工核对项目事实与评分办法后使用。"],
    }


def build_defense_docx(outline: dict[str, Any]) -> bytes:
    document = DocxDocument()
    project = outline.get("project") or {}
    document.add_heading(f"{project.get('name', '项目')} · 答辩提纲", level=1)
    document.add_paragraph(f"建议时长：{outline.get('minutes', 20)} 分钟")
    document.add_paragraph("说明：本提纲为结构化草稿，需人工核对项目事实与评分办法后使用。")
    for index, slide in enumerate(outline.get("slides") or [], start=1):
        document.add_heading(f"{index}. {slide.get('title', '')}（{slide.get('minutes', 0)} 分钟）", level=2)
        for point in slide.get("points") or []:
            document.add_paragraph(str(point), style="List Bullet")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
