"""决策证据链接口（P1-8）。

独立路由文件：不修改既有 ``projects/router.py``，由 ``app/main.py`` 统一注册，
避免与并行开发中的其他模块产生冲突。

只读接口：不写库、不调用外部网关、不消耗计量；所有数据来自本租户已有表。
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.decision_evidence import (
    DecisionEvidenceResponse,
    collect_decision_evidence,
)

router = APIRouter(prefix="/api/v1", tags=["decision-evidence"])


@router.get(
    "/projects/{project_id}/decision-evidence",
    response_model=DecisionEvidenceResponse,
)
async def get_decision_evidence(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> DecisionEvidenceResponse:
    """「投不投」证据链：采购人历史中标、竞争强度、我方胜率、预算锚点。

    每项都带取数口径（时间窗、条数、匹配方式、来源表），样本不足显式标记
    ``scored=false`` 并给出原因，不输出中位数/胜率等伪统计。
    """
    payload = await collect_decision_evidence(
        session, tenant_id=user.tenant_id, project_id=project_id
    )
    return DecisionEvidenceResponse.model_validate(payload)
