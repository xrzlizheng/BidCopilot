from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import Company
from app.modules.identity.models import Tenant, User
from app.modules.projects.commitments import list_commitments
from app.modules.projects.models import BidCommitment, BidProject, TenderDocument
from app.modules.projects.service import get_project


async def run_self_check(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
) -> dict[str, Any]:
    project = await get_project(session, tenant_id, project_id)
    findings: list[dict[str, Any]] = []
    current_commitments = await list_commitments(session, tenant_id, project_id)
    personnel_names = {
        commitment.value_text
        for commitment in current_commitments
        if commitment.kind == "personnel" and commitment.value_text
    }

    other_projects = list(
        (
            await session.execute(
                select(BidProject).where(
                    BidProject.tenant_id == tenant_id,
                    BidProject.status == "active",
                    BidProject.id != project_id,
                )
            )
        ).scalars()
    )
    other_project_ids = [other.id for other in other_projects]
    commitments_by_project: dict[UUID, set[str]] = {}
    if other_project_ids and personnel_names:
        other_commitments_rows = list(
            (
                await session.execute(
                    select(BidCommitment.project_id, BidCommitment.value_text).where(
                        BidCommitment.tenant_id == tenant_id,
                        BidCommitment.project_id.in_(other_project_ids),
                        BidCommitment.kind == "personnel",
                    )
                )
            ).all()
        )
        for pid, name in other_commitments_rows:
            if name:
                commitments_by_project.setdefault(pid, set()).add(name)

    for other in other_projects:
        other_names = commitments_by_project.get(other.id, set())
        overlap = sorted(personnel_names & other_names)
        if overlap:
            findings.append(
                {
                    "code": "PERSONNEL_CROSS_PROJECT",
                    "severity": "high",
                    "message": f"拟派人员同时出现在其他投标项目：{'、'.join(overlap)}",
                    "evidence": {"other_project": other.name, "personnel": overlap},
                }
            )
        if (
            project.owner_id is not None
            and other.owner_id == project.owner_id
            and project.bid_deadline is not None
            and other.bid_deadline is not None
            and abs((other.bid_deadline - project.bid_deadline).days) <= 7
        ):
            findings.append(
                {
                    "code": "OWNER_MULTI_PROJECT",
                    "severity": "medium",
                    "message": f"同一负责人同时负责 7 天内的另一个投标项目：{other.name}",
                    "evidence": {"other_project": other.name},
                }
            )

    final_doc = (
        await session.execute(
            select(TenderDocument)
            .where(
                TenderDocument.tenant_id == tenant_id,
                TenderDocument.project_id == project_id,
                TenderDocument.document_kind == "final",
            )
            .order_by(TenderDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    metadata = (final_doc.assembly_meta or {}).get("metadata") if final_doc else None
    if metadata:
        allowed: list[str] = []
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None:
            allowed.append(tenant.name)
        if project.company_id:
            company = await session.get(Company, project.company_id)
            if company is not None:
                allowed.append(company.name)
        if project.owner_id:
            owner = await session.get(User, project.owner_id)
            if owner is not None:
                allowed.append(owner.display_name)
        for field in ("author", "last_modified_by"):
            value = metadata.get(field)
            if not value:
                continue
            text = str(value).strip()
            if text and not any(name and (name in text or text in name) for name in allowed):
                findings.append(
                    {
                        "code": "DOC_METADATA_MISMATCH",
                        "severity": "medium",
                        "message": (
                            f"最终文件元数据 {field}={text} 与投标主体名称不一致；"
                            "请确认文件由本单位独立编制。系统只提示，不提供元数据清理/规避建议。"
                        ),
                        "evidence": {"field": field, "value": text},
                    }
                )

    return {
        "project_id": str(project.id),
        "findings": findings,
        "checked": {
            "personnel": len(personnel_names),
            "other_active_projects": len(other_projects),
            "final_document": bool(final_doc),
        },
        "notes": ["只做合规提示与冲突预防，不提供元数据清理、痕迹清理、降重或规避检测能力。"],
    }
