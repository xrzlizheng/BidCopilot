from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.locks import advisory_lock
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.evaluation.models import BidEvaluation
from app.modules.evaluation.schemas import EvaluationListItem, EvaluationResponse
from app.modules.evaluation.service import (
    EvaluationFailedError,
    evaluate_project,
    get_evaluation,
    list_evaluations,
)
from app.modules.generation.deps import get_judge_client, get_llm_client
from app.modules.generation.llm_client import LLMClient
from app.modules.generation.service import LLMNotConfiguredError
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


def _list_item(record: BidEvaluation) -> EvaluationListItem:
    summary = record.result_json.get("summary") if isinstance(record.result_json, dict) else {}
    return EvaluationListItem(
        id=record.id,
        project_id=record.project_id,
        status=record.status,
        model=record.model,
        created_at=record.created_at,
        finished_at=record.finished_at,
        summary=summary if isinstance(summary, dict) else {},
        error_code=record.error_code,
        error_message=record.error_message,
    )


@router.post(
    "/projects/{project_id}/evaluations",
    response_model=EvaluationResponse,
    status_code=201,
)
async def post_evaluation(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:write")),
    session: AsyncSession = Depends(get_session),
    llm_client: LLMClient = Depends(get_judge_client),
    fallback_client: LLMClient = Depends(get_llm_client),
) -> EvaluationResponse:
    try:
        # 评标调用付费判别模型，按项目互斥，避免重复点击造成重复计费
        async with advisory_lock(f"project-evaluation:{project_id}") as acquired:
            if not acquired:
                raise ConflictError("该项目的 AI 评标正在进行中，请稍后重试")
            record = await evaluate_project(
                session,
                tenant_id=user.tenant_id,
                project_id=project_id,
                user_id=user.id,
                llm_client=llm_client,
                fallback_client=fallback_client,
            )
    except (LLMNotConfiguredError, EvaluationFailedError) as exc:
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="project.evaluate",
            resource_type="bid_project",
            resource_id=str(project_id),
            data={"status": "failed", "code": exc.code, "message": exc.message},
        )
        await session.commit()
        raise
    summary = record.result_json.get("summary") if isinstance(record.result_json, dict) else {}
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="project.evaluate",
        resource_type="bid_evaluation",
        resource_id=str(record.id),
        data={
            "project_id": str(project_id),
            "status": record.status,
            "percentage": summary.get("percentage") if isinstance(summary, dict) else None,
            "model": record.model,
        },
    )
    await session.commit()
    return EvaluationResponse.model_validate(record)


@router.get(
    "/projects/{project_id}/evaluations",
    response_model=list[EvaluationListItem],
)
async def get_evaluations(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> list[EvaluationListItem]:
    records = await list_evaluations(session, user.tenant_id, project_id)
    return [_list_item(record) for record in records]


@router.get(
    "/projects/{project_id}/evaluations/latest",
    response_model=EvaluationResponse,
)
async def get_latest_evaluation(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> EvaluationResponse:
    records = await list_evaluations(session, user.tenant_id, project_id)
    if not records:
        raise NotFoundError("评标记录不存在")
    return EvaluationResponse.model_validate(records[0])


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationResponse)
async def get_evaluation_detail(
    evaluation_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> EvaluationResponse:
    record = await get_evaluation(session, user.tenant_id, evaluation_id)
    return EvaluationResponse.model_validate(record)
