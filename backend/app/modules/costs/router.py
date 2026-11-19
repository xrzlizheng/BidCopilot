from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.costs.service import data_ops_summary
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1/analytics", tags=["data-ops"])


@router.get("/data-ops")
async def get_data_ops(
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await data_ops_summary(session, user.tenant_id, days)
