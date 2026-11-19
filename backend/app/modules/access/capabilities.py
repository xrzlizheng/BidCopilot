from __future__ import annotations

CAPABILITY_DOMAINS = frozenset(
    {
        "tenant",
        "user",
        "role",
        "org",
        "asset",
        "knowledge",
        "template",
        "model",
        "audit",
        "compliance",
        "project",
        "opportunity",
    }
)

BASE_CAPABILITIES = frozenset(
    {
        "*",
        "tenant:read",
        "tenant:settings:manage",
        "user:read",
        "user:manage",
        "role:read",
        "role:manage",
        "role:assign",
        "org:read",
        "org:manage",
        "asset:read",
        "asset:write",
        "asset:import",
        "asset:delete",
        "asset:export",
        "asset:approve",
        "knowledge:read",
        "knowledge:use",
        "knowledge:write",
        "knowledge:publish",
        "knowledge:manage",
        "knowledge:export",
        "template:read",
        "template:use",
        "template:copy",
        "template:write",
        "template:publish",
        "template:manage",
        "model:use",
        "model:read_metadata",
        "model:manage",
        "model:manage_key",
        "model:policy:manage",
        "audit:read",
        "compliance:review",
        "compliance:break_glass",
        "compliance:export",
        "project:read",
        "project:write",
        "project:approve",
        "project:export",
        "opportunity:read",
        "opportunity:write",
        "opportunity:sync",
    }
)


def is_capability(value: str) -> bool:
    if value in BASE_CAPABILITIES:
        return True
    if value.endswith(":*"):
        return value[:-2] in CAPABILITY_DOMAINS
    return False


def validate_permissions(values: list[str] | tuple[str, ...]) -> None:
    unknown = sorted({value for value in values if not is_capability(value)})
    if unknown:
        raise ValueError(f"unknown capability: {', '.join(unknown)}")


# 企业版扩展点：overlay 提供 caps_extra.py 时注入额外能力域与能力词。
try:  # noqa: SIM105
    from app.modules.access.caps_extra import (
        EXTRA_BASE_CAPABILITIES as _EXTRA_CAPS,
        EXTRA_CAPABILITY_DOMAINS as _EXTRA_DOMAINS,
    )
except ImportError:
    _EXTRA_DOMAINS: frozenset[str] = frozenset()
    _EXTRA_CAPS: frozenset[str] = frozenset()

CAPABILITY_DOMAINS = CAPABILITY_DOMAINS | _EXTRA_DOMAINS
BASE_CAPABILITIES = BASE_CAPABILITIES | _EXTRA_CAPS
