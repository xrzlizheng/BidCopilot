import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://bidcopilot:bidcopilot@localhost:55432/bidcopilot_test",
)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault(
    "MODEL_SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
os.environ.setdefault("MODEL_BYOK_ENABLED", "true")
os.environ.setdefault("MODEL_ALLOW_PRIVATE_BASE_URL", "true")
os.environ.setdefault("SNAPSHOT_STORE", "local")
os.environ.setdefault("SNAPSHOT_LOCAL_DIR", "./.snapshots-test")

# Keep the suite deterministic even when backend/.env contains real providers.
for key in (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "JUDGE_BASE_URL",
    "JUDGE_API_KEY",
    "JUDGE_MODEL",
    "MINIMAX_API_KEY",
    "MINIMAX_API_HOST",
    "DEEPSEEK_SEARCH_API_KEY",
    "MINIMAX_SEARCH_API_KEY",
):
    os.environ[key] = ""
