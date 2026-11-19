from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceUnavailableError
from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.deps import get_web_search_client
from app.modules.gateway.web_search import (
    WebSearchClient,
    WebSearchConfigError,
    WebSearchResponseError,
    WebSearchTransportError,
)
from app.modules.identity.deps import require_permission
from app.modules.identity.schemas import CurrentUser
from app.modules.web_search.schemas import (
    WebSearchItemResponse,
    WebSearchRequest,
    WebSearchResponse,
)

router = APIRouter(prefix="/api/v1", tags=["web-search"])


@router.post("/web-search", response_model=WebSearchResponse)
async def search_web(
    body: WebSearchRequest,
    user: CurrentUser = Depends(require_permission("project:read")),
    session: AsyncSession = Depends(get_session),
    client: WebSearchClient = Depends(get_web_search_client),
) -> WebSearchResponse:
    if not client.configured():
        raise ServiceUnavailableError("联网检索服务未配置")
    query = body.query.strip()
    digest = query_hash({"query": query, "count": body.count})
    reservation = await reserve_paid_call(user.tenant_id)
    try:
        page = await client.search(query, count=body.count)
    except (WebSearchConfigError, WebSearchTransportError, WebSearchResponseError) as exc:
        await release_paid_call(reservation)
        await record_gateway_call(
            session,
            tenant_id=user.tenant_id,
            pcode=f"web_search:{client.provider}",
            operation="web.search",
            status="failed",
            order_no=None,
            charged=False,
            rows=0,
            query_hash_value=digest,
            resource_type="web_search",
            resource_id=digest,
            created_by=user.id,
        )
        await AuditService(session).append(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="research.web_search",
            resource_type="web_search",
            resource_id=digest,
            data={"provider": client.provider, "status": "failed", "error": str(exc)},
        )
        await session.commit()
        raise ServiceUnavailableError("联网检索服务暂不可用") from exc

    await record_gateway_call(
        session,
        tenant_id=user.tenant_id,
        pcode=f"web_search:{page.provider}",
        operation="web.search",
        status=page.status,
        order_no=None,
        charged=True,
        rows=len(page.items),
        query_hash_value=digest,
        resource_type="web_search",
        resource_id=digest,
        created_by=user.id,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="research.web_search",
        resource_type="web_search",
        resource_id=digest,
        data={
            "provider": page.provider,
            "status": page.status,
            "rows": len(page.items),
            "raw_ref": page.raw_ref,
        },
    )
    await session.commit()
    return WebSearchResponse(
        status=page.status,
        provider=page.provider,
        query=page.query,
        results=[
            WebSearchItemResponse(
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                published_at=item.published_at,
            )
            for item in page.items
        ],
        raw_ref=page.raw_ref,
        charged=True,
        fetched_at=page.fetched_at,
    )
