from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.projects.results import bid_result_analytics

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/bid-results")
async def get_bid_results(
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await bid_result_analytics(session, user.tenant_id)
