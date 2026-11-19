"""AI 用量查询接口：按项目统计。

设计说明：
- 路由独立成文件（`ai_usage_router.py`），prefix 为 `/api/v1`，不改变既有
  `costs/router.py` 的 URL。
- 计量写入不写审计（避免每篇都产生噪声）；用量读取属常规查询。
- 跨租户按 ID 读取一律 404（经 `get_project` 的租户过滤），不泄露资源存在性。
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.costs.ai_usage_schemas import AiUsageResponse
from app.modules.costs.metering import usage_by_project
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.service import get_project

router = APIRouter(prefix="/api/v1", tags=["ai-usage"])


@router.get("/projects/{project_id}/ai-usage", response_model=AiUsageResponse)
async def get_project_ai_usage(
    project_id: UUID,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> AiUsageResponse:
    # get_project 已做租户过滤，跨租户 ID 直接 404
    await get_project(session, user.tenant_id, project_id)
    usage = await usage_by_project(session, user.tenant_id, project_id)
    return AiUsageResponse(**usage)
