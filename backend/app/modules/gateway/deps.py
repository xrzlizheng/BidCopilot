from app.config import get_settings
from app.modules.gateway.company_adapter import KeepRiskCompanyIntelAdapter
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter
from app.modules.gateway.keeprisk_client import KeepRiskClient
from app.modules.gateway.keeprisk_mcp import KeepRiskMcpClient
from app.modules.gateway.snapshots import build_snapshot_store
from app.modules.gateway.web_search import WebSearchClient


def _build_client() -> KeepRiskClient:
    settings = get_settings()
    return KeepRiskClient(
        base_url=settings.KEEPRISK_API_BASE_URL,
        app_code=settings.KEEPRISK_APP_CODE,
        timeout_seconds=settings.KEEPRISK_TIMEOUT_SECONDS,
        max_response_bytes=settings.KEEPRISK_MAX_RESPONSE_BYTES,
        snapshot_store=build_snapshot_store(settings),
    )


def get_keeprisk_adapter() -> KeepRiskTenderAdapter:
    return KeepRiskTenderAdapter(_build_client())


def get_company_intel_adapter() -> KeepRiskCompanyIntelAdapter:
    return KeepRiskCompanyIntelAdapter(_build_client())


def get_enterprise_intel_adapter() -> EnterpriseIntelAdapter:
    settings = get_settings()
    client = KeepRiskMcpClient(
        url=settings.KEEPRISK_MCP_URL if settings.KEEPRISK_MCP_ENABLED else None,
        token=settings.KEEPRISK_MCP_TOKEN if settings.KEEPRISK_MCP_ENABLED else None,
        timeout_seconds=settings.KEEPRISK_MCP_TIMEOUT_SECONDS,
        max_response_bytes=settings.KEEPRISK_MCP_MAX_RESPONSE_BYTES,
        snapshot_store=build_snapshot_store(settings),
    )
    return EnterpriseIntelAdapter(client)


def get_web_search_client() -> WebSearchClient:
    settings = get_settings()
    return WebSearchClient(
        provider=settings.WEB_SEARCH_PROVIDER,
        timeout_seconds=settings.WEB_SEARCH_TIMEOUT_SECONDS,
        max_results=settings.WEB_SEARCH_MAX_RESULTS,
        deepseek_api_key=settings.DEEPSEEK_SEARCH_API_KEY,
        deepseek_base_url=settings.DEEPSEEK_SEARCH_BASE_URL,
        minimax_search_key=settings.MINIMAX_SEARCH_API_KEY or settings.MINIMAX_API_KEY,
        minimax_base_url=settings.MINIMAX_SEARCH_BASE_URL,
        minimax_model=settings.MINIMAX_SEARCH_MODEL,
        snapshot_store=build_snapshot_store(settings),
    )
