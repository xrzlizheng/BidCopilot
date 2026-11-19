from app.core.permissions import has_permission


def test_wildcard_permission() -> None:
    assert has_permission(["asset:*"], "asset:write")
    assert has_permission(["platform:*"], "platform:tenant:manage")
    assert has_permission(["*"], "anything")


def test_missing_permission() -> None:
    assert not has_permission(["asset:read"], "asset:write")
    assert not has_permission([], "opportunity:read")
