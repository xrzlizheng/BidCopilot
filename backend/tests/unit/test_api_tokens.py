import hashlib
from uuid import uuid4

import pytest

from app.config import get_settings
from app.core.errors import ValidationAppError
from app.modules.identity import api_tokens


class FakeSession:
    """最小 AsyncSession 替身：只覆盖 create_token / authenticate 用到的调用。"""

    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0
        self._lookup = None

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row) -> None:
        return None

    async def execute(self, statement):
        class _Result:
            def scalar_one_or_none(self_inner):
                return self._lookup

        return _Result()


def test_generated_token_has_prefix_and_entropy() -> None:
    value = api_tokens.generate_token_value()
    assert value.startswith("btc_")
    # 32 字节 urlsafe base64 ≈ 43 字符合理随机串。
    assert len(value) >= 40
    values = {api_tokens.generate_token_value() for _ in range(200)}
    assert len(values) == 200


def test_hash_is_sha256_hex_and_prefix_is_short() -> None:
    value = "btc_example-token-value-for-unit-test"
    digest = api_tokens.hash_token(value)
    assert digest == hashlib.sha256(value.encode()).hexdigest()
    assert len(digest) == 64
    prefix = api_tokens.token_prefix(value)
    assert value.startswith(prefix)
    assert len(prefix) < len(value)
    # 前缀不足以反推明文：sha256 全量相等 ≠ 前缀相等。
    assert api_tokens.hash_token(prefix) != digest


def test_is_api_token_value_only_matches_btc_prefix() -> None:
    assert api_tokens.is_api_token_value("btc_abc")
    assert not api_tokens.is_api_token_value("eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert not api_tokens.is_api_token_value("")


async def test_create_token_stores_hash_not_plaintext() -> None:
    session = FakeSession()
    tenant_id, user_id = uuid4(), uuid4()
    row, plaintext = await api_tokens.create_token(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        name="集成商 A",
        scopes=["project:read", "compliance:review"],
    )
    # 明文只返回一次；库里只有哈希与前缀。
    assert plaintext.startswith(api_tokens.TOKEN_PREFIX)
    assert row.token_hash == api_tokens.hash_token(plaintext)
    assert plaintext not in (row.token_hash, row.token_prefix)
    assert row.token_prefix == plaintext[: api_tokens.PREFIX_DISPLAY_LENGTH]
    assert row.scopes == ["project:read", "compliance:review"]
    assert row.expires_at is None
    assert session.commits == 1


async def test_create_token_with_expiry_sets_future_timestamp() -> None:
    session = FakeSession()
    row, _ = await api_tokens.create_token(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="有期限 token",
        scopes=["project:read"],
        expires_in_days=30,
    )
    assert row.expires_at is not None


async def test_create_token_rejects_empty_name_and_scopes() -> None:
    session = FakeSession()
    with pytest.raises(ValidationAppError):
        await api_tokens.create_token(
            session, tenant_id=uuid4(), user_id=uuid4(), name="   ", scopes=["project:read"]
        )
    with pytest.raises(ValidationAppError):
        await api_tokens.create_token(
            session, tenant_id=uuid4(), user_id=uuid4(), name="ok", scopes=["  "]
        )
    with pytest.raises(ValidationAppError):
        await api_tokens.create_token(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            name="ok",
            scopes=["project:read"],
            expires_in_days=0,
        )


async def test_create_token_deduplicates_scopes() -> None:
    row, _ = await api_tokens.create_token(
        FakeSession(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="重复 scope",
        scopes=["project:read", "project:read", "compliance:review"],
    )
    assert row.scopes == ["project:read", "compliance:review"]


def test_effective_permissions_is_scope_intersection() -> None:
    """scopes 与创建者当前权限取交集：创建者失去的能力 token 立刻失去。"""
    from app.modules.identity.api_token_models import ApiToken
    from app.modules.identity.schemas import CurrentUser

    token = ApiToken(
        tenant_id=uuid4(),
        name="t",
        token_prefix="btc_abcdef",
        token_hash="0" * 64,
        scopes=["project:read", "compliance:review", "model:manage"],
    )
    creator = CurrentUser(
        id=uuid4(),
        tenant_id=token.tenant_id,
        email="creator@example.com",
        display_name="Creator",
        roles=["tenant_admin"],
        permissions=["project:read", "compliance:review", "project:*"],
    )
    granted = api_tokens._effective_permissions(token, creator)
    # model:manage 创建者已无权限 → token 不得放大。
    assert granted == ["project:read", "compliance:review"]


def test_effective_permissions_honours_wildcard() -> None:
    from app.modules.identity.api_token_models import ApiToken
    from app.modules.identity.schemas import CurrentUser

    token = ApiToken(
        tenant_id=uuid4(),
        name="t",
        token_prefix="btc_abcdef",
        token_hash="0" * 64,
        scopes=["project:read"],
    )
    creator = CurrentUser(
        id=uuid4(),
        tenant_id=token.tenant_id,
        email="a@example.com",
        display_name="A",
        roles=[],
        permissions=["*"],
    )
    assert api_tokens._effective_permissions(token, creator) == ["project:read"]


def test_webhook_config_defaults_are_bounded() -> None:
    settings = get_settings()
    assert settings.WEBHOOK_MAX_ATTEMPTS >= 1
    assert 0 < settings.WEBHOOK_TIMEOUT_SECONDS <= 120
    # 默认不允许私网 webhook：裸配置也不该放开 SSRF 闸门。
    assert settings.WEBHOOK_ALLOW_PRIVATE_URL is False
