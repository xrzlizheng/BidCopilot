from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import engine
from app.modules.assets.router import router as assets_router
from app.modules.audit.router import router as audit_router
from app.modules.company_intel.intel_router import router as company_intel_mcp_router
from app.modules.company_intel.router import router as company_intel_router
from app.modules.compliance.access_router import router as compliance_access_router
from app.modules.compliance.personal_resources_router import (
    router as compliance_personal_router,
)
from app.modules.compliance.router import router as compliance_router
from app.modules.contracts.lifecycle_router import router as contract_lifecycle_router
from app.modules.contracts.router import router as contracts_router
from app.modules.costs.ai_usage_router import router as ai_usage_router
from app.modules.costs.router import router as costs_router
from app.modules.evaluation.router import router as evaluation_router
from app.modules.file_check.router import router as file_check_router
from app.modules.generation.router import router as generation_router
from app.modules.identity.api_tokens_router import router as api_tokens_router
from app.modules.identity.router import router as auth_router
from app.modules.identity.users import router as users_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.notifications.channel_router import router as notification_channels_router
from app.modules.notifications.router import router as notifications_router
from app.modules.notifications.webhook_router import router as webhooks_router
from app.modules.opportunities.router import router as opportunities_router
from app.modules.opportunities.subscriptions_router import (
    router as opportunity_subscriptions_router,
)
from app.modules.org.router import router as org_router
from app.modules.pricing.router import router as pricing_router
from app.modules.projects.analytics import router as analytics_router
from app.modules.projects.decision_evidence_router import router as decision_evidence_router
from app.modules.projects.router import router as projects_router
from app.modules.system.router import router as system_router
from app.modules.web_search.router import router as web_search_router
from app.workers.scheduler import build_scheduler

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        scheduler = build_scheduler()
        scheduler.start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await engine.dispose()


app = FastAPI(title="BidCopilot API", version=settings.APP_VERSION, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(org_router)
app.include_router(knowledge_router)
# 企业版扩展点：overlay 提供 platform 模块时挂载其路由（社区版不存在）。
try:
    from app.modules.platform.router import router as platform_router

    app.include_router(platform_router)
except ImportError:
    pass
app.include_router(audit_router)
app.include_router(opportunities_router)
app.include_router(opportunity_subscriptions_router)
app.include_router(notifications_router)
app.include_router(notification_channels_router)
app.include_router(projects_router)
app.include_router(decision_evidence_router)
app.include_router(pricing_router)
app.include_router(analytics_router)
app.include_router(generation_router)
app.include_router(evaluation_router)
app.include_router(file_check_router)
app.include_router(costs_router)
app.include_router(ai_usage_router)
app.include_router(compliance_router)
app.include_router(compliance_access_router)
app.include_router(compliance_personal_router)
app.include_router(contracts_router)
app.include_router(contract_lifecycle_router)
app.include_router(assets_router)
app.include_router(company_intel_router)
app.include_router(company_intel_mcp_router)
app.include_router(api_tokens_router)
app.include_router(webhooks_router)
app.include_router(system_router)
app.include_router(web_search_router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/readyz", tags=["system"])
async def readyz() -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    try:
        client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        try:
            await client.ping()
        finally:
            await client.aclose()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
