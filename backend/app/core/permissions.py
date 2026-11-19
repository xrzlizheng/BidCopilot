def has_permission(granted: list[str] | tuple[str, ...], required: str) -> bool:
    for permission in granted:
        if permission == "*" or permission == required:
            return True
        if permission.endswith(":*") and required.startswith(permission[:-1]):
            return True
    return False
