from uuid import uuid4

import pytest

from app.core.jwt import TokenError, create_access_token, create_refresh_token, decode_token


def test_access_token_roundtrip() -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=["tenant_admin"],
        permissions=["asset:*"],
        token_version=3,
    )

    payload = decode_token(token, "access")

    assert payload["sub"] == str(user_id)
    assert payload["tid"] == str(tenant_id)
    assert payload["perms"] == ["asset:*"]
    assert payload["ver"] == 3


def test_refresh_token_type_is_enforced() -> None:
    token = create_refresh_token(user_id=uuid4(), tenant_id=uuid4())

    with pytest.raises(TokenError):
        decode_token(token, "access")
