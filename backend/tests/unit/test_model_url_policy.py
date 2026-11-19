import pytest

from app.config import get_settings
from app.core.errors import ValidationAppError
from app.modules.system.url_policy import validate_model_base_url


@pytest.mark.asyncio
async def test_model_base_url_rejects_private_and_non_http(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "MODEL_ALLOW_PRIVATE_BASE_URL", False)
    monkeypatch.setattr(settings, "MODEL_ALLOWED_HOSTS", "")

    for url in (
        "http://127.0.0.1:9999/v1",
        "http://10.0.0.8/v1",
        "http://169.254.169.254/latest/meta-data",
        "ftp://example.com/v1",
    ):
        with pytest.raises(ValidationAppError):
            await validate_model_base_url(url)

    await validate_model_base_url("https://8.8.8.8/v1")


@pytest.mark.asyncio
async def test_model_base_url_allowlist_permits_private_host(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "MODEL_ALLOW_PRIVATE_BASE_URL", False)
    monkeypatch.setattr(settings, "MODEL_ALLOWED_HOSTS", "127.0.0.1,llm.internal")
    await validate_model_base_url("http://127.0.0.1:9999/v1")
