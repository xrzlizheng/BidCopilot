import pytest

from app.modules.access.capabilities import (
    BASE_CAPABILITIES,
    is_capability,
    validate_permissions,
)
from app.modules.identity.roles import SYSTEM_ROLES


def test_every_system_role_permission_is_registered() -> None:
    for spec in SYSTEM_ROLES.values():
        for permission in spec["permissions"]:
            assert is_capability(permission), permission


def test_unknown_capability_is_rejected() -> None:
    assert not is_capability("knowledge:fly")
    with pytest.raises(ValueError, match="unknown capability"):
        validate_permissions(["knowledge:fly"])


def test_actions_and_wildcards_are_registered() -> None:
    assert "knowledge:use" in BASE_CAPABILITIES
    assert "template:publish" in BASE_CAPABILITIES
    assert is_capability("knowledge:*")
    assert is_capability("project:*")
    assert is_capability("*")
