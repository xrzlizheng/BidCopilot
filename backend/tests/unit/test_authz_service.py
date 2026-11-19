from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError
from app.modules.access.service import AuthzService, DenyReason
from app.modules.identity.schemas import CurrentUser


def _user(*permissions: str) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        tenant_id=uuid4(),
        email="u@example.com",
        display_name="u",
        roles=[],
        permissions=list(permissions),
        tenant_name="t",
        token_version=0,
    )


def test_capability_allows_wildcard() -> None:
    decision = AuthzService.can_capability(_user("knowledge:*"), "knowledge:read")
    assert decision.allow


def test_capability_denies_missing() -> None:
    decision = AuthzService.can_capability(_user("project:read"), "knowledge:read")
    assert not decision.allow
    assert decision.reason_code == DenyReason.NO_CAPABILITY


def test_require_capability_raises_forbidden() -> None:
    with pytest.raises(ForbiddenError) as exc:
        AuthzService.require_capability(_user("project:read"), "knowledge:read")
    assert exc.value.code == "no_capability"


def test_unknown_capability_fails_closed() -> None:
    decision = AuthzService.can_capability(_user("*"), "knowledge:fly")
    assert not decision.allow
    assert decision.reason_code == DenyReason.UNKNOWN_CAPABILITY
