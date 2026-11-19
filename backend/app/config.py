from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_ENV: str = "development"
    APP_VERSION: str = "0.1.0"
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ACCESS_TTL_SECONDS: int = 900
    JWT_REFRESH_TTL_SECONDS: int = 604800
    LOGIN_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=100)
    LOGIN_IP_MAX_ATTEMPTS: int = Field(default=20, ge=1, le=1000)
    LOGIN_WINDOW_SECONDS: int = Field(default=300, ge=10, le=86400)

    KEEPRISK_API_BASE_URL: str | None = None
    KEEPRISK_APP_CODE: str | None = None
    KEEPRISK_TIMEOUT_SECONDS: float = 15.0
    KEEPRISK_MAX_RESPONSE_BYTES: int = 20 * 1024 * 1024

    KEEPRISK_MCP_ENABLED: bool = False
    KEEPRISK_MCP_URL: str = "https://mcp.keeprisk.com/mcp"
    KEEPRISK_MCP_TOKEN: str | None = None
    KEEPRISK_MCP_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, le=300)
    KEEPRISK_MCP_MAX_RESPONSE_BYTES: int = 20 * 1024 * 1024

    LLM_BASE_URL: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_API_BASE"),
    )
    LLM_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    LLM_MODEL: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_MODEL", "OPENAI_MODEL"),
    )
    LLM_TIMEOUT_SECONDS: float = 60.0

    # 合同 LLM 深审分批：单批字符预算（按条款聚合，防长合同整篇单 prompt 溢出）
    # 与批间并发上限（全仓唯一的并发 LLM 调用点，沿用 OpenAI 兼容网关 RPM 习惯）。
    CONTRACT_DEEP_REVIEW_BATCH_CHARS: int = Field(default=6000, ge=1000, le=20000)
    CONTRACT_DEEP_REVIEW_CONCURRENCY: int = Field(default=3, ge=1, le=8)

    JUDGE_BASE_URL: str | None = None
    JUDGE_API_KEY: str | None = None
    JUDGE_MODEL: str | None = None
    JUDGE_TIMEOUT_SECONDS: float = 120.0

    MODEL_SECRET_MASTER_KEY: str | None = None
    MODEL_SECRET_KEY_VERSION: int = 1
    MODEL_BYOK_ENABLED: bool = False
    MODEL_ALLOW_PERSONAL_KEY_FOR_ENTERPRISE_DATA: bool = False
    MODEL_ALLOW_PRIVATE_BASE_URL: bool = False
    MODEL_ALLOWED_HOSTS: str = ""
    BREAK_GLASS_MAX_MINUTES: int = Field(default=60, ge=1, le=1440)

    MINIMAX_API_KEY: str | None = None
    MINIMAX_API_HOST: str | None = None
    MINIMAX_OCR_MAX_PAGES: int = Field(default=10, ge=1, le=50)
    MINIMAX_OCR_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0, le=600)

    OCR_PROVIDER: str = "auto"
    OCR_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0, le=600)
    MINERU_TIER: str = "auto"
    MINERU_GPU_MIN_VRAM_GB: float = Field(default=8.0, gt=0, le=256)
    MINERU_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0, le=1800)

    # 规则抽取之后的兜底层（默认全关；产出以 confidence="uie"/"llm" 标记）
    TENDER_UIE_FALLBACK_ENABLED: bool = False
    TENDER_LLM_FALLBACK_ENABLED: bool = False

    WEB_SEARCH_PROVIDER: str = "deepseek"
    WEB_SEARCH_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=120)
    WEB_SEARCH_MAX_RESULTS: int = Field(default=10, ge=1, le=10)
    DEEPSEEK_SEARCH_API_KEY: str | None = None
    DEEPSEEK_SEARCH_BASE_URL: str = "https://api.deepseek.com"
    MINIMAX_SEARCH_API_KEY: str | None = None
    MINIMAX_SEARCH_BASE_URL: str = "https://api.minimax.cn/anthropic"
    MINIMAX_SEARCH_MODEL: str = "MiniMax-M3"

    SNAPSHOT_STORE: str = "minio"
    SNAPSHOT_LOCAL_DIR: str = "./.snapshots"
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "bidcopilot"
    MINIO_SECURE: bool = False

    SCHEDULER_ENABLED: bool = False
    PARSE_JOB_STALE_MINUTES: int = Field(default=15, ge=1, le=1440)
    PARSE_JOB_RECOVERY_BATCH: int = Field(default=5, ge=1, le=50)
    # 生成/评标任务回收阈值：进程内同步执行、无心跳，只能按创建时间判超时；
    # 调大前先确认 LLM 长文生成的最长耗时，避免把正常任务误判 failed。
    AI_RUN_STALE_MINUTES: int = Field(default=30, ge=1, le=1440)
    PAID_CALL_DAILY_LIMIT: int = Field(default=1000, ge=0, le=1_000_000)
    PAID_CALL_MONTHLY_LIMIT: int = Field(default=20000, ge=0, le=10_000_000)
    INTEL_WATCHLIST_ENABLED: bool = True
    INTEL_WATCHLIST_INTERVAL_HOURS: int = Field(default=24, ge=1, le=168)
    INTEL_WATCHLIST_MAX_ITEMS: int = Field(default=20, ge=0, le=200)
    INTEL_WATCHLIST_BATCH_SIZE: int = Field(default=50, ge=1, le=200)

    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None
    SMTP_USE_TLS: bool = True
    OPPORTUNITY_MAX_SUBSCRIPTIONS_PER_TENANT: int = Field(default=10, ge=1, le=100)
    SYNC_MIN_INTERVAL_MINUTES: int = Field(default=30, ge=1, le=1440)

    # Webhook 出站（事件通知给集成商）。重试与超时必须同时有界，
    # 避免下游慢/挂导致投递任务堆积；退避系数可配以便测试关闭等待。
    WEBHOOK_DELIVERY_ENABLED: bool = True
    WEBHOOK_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=120)
    WEBHOOK_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    WEBHOOK_RETRY_BACKOFF_SECONDS: float = Field(default=0.5, ge=0, le=30)
    # 与 MODEL_ALLOWED_HOSTS 同口径的运维逃生口：仅显式列出时才跳过私网判定。
    WEBHOOK_ALLOWED_HOSTS: str = ""
    WEBHOOK_ALLOW_PRIVATE_URL: bool = False

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _reject_weak_secret(cls, value: str) -> str:
        lowered = value.strip().lower()
        weak = {"change-me", "changeme", "password", "secret", "test-secret", "dev-secret"}
        if lowered in weak:
            raise ValueError("JWT_SECRET_KEY is too weak")
        placeholder_markers = (
            "replace",
            "placeholder",
            "your-",
            "your_",
            "example",
            "sample",
            "dummy",
            "changeme",
            "change-me",
        )
        if any(marker in lowered for marker in placeholder_markers):
            raise ValueError("JWT_SECRET_KEY must not be a placeholder value")
        if len(set(value)) < 8:
            raise ValueError("JWT_SECRET_KEY has insufficient entropy")
        return value

    @field_validator("SNAPSHOT_STORE")
    @classmethod
    def _validate_snapshot_store(cls, value: str) -> str:
        if value not in {"minio", "local"}:
            raise ValueError("SNAPSHOT_STORE must be minio or local")
        return value

    @field_validator("WEB_SEARCH_PROVIDER")
    @classmethod
    def _validate_web_search_provider(cls, value: str) -> str:
        if value not in {"deepseek", "minimax"}:
            raise ValueError("WEB_SEARCH_PROVIDER must be deepseek or minimax")
        return value

    @field_validator("OCR_PROVIDER")
    @classmethod
    def _validate_ocr_provider(cls, value: str) -> str:
        if value not in {"auto", "minimax", "mineru", "none"}:
            raise ValueError("OCR_PROVIDER must be auto, minimax, mineru or none")
        return value

    @field_validator("MINERU_TIER")
    @classmethod
    def _validate_mineru_tier(cls, value: str) -> str:
        if value not in {"auto", "flash", "basic", "standard", "advanced"}:
            raise ValueError("MINERU_TIER must be auto, flash, basic, standard or advanced")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
