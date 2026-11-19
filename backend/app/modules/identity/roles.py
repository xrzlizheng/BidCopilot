from typing import TypedDict


class RoleSpec(TypedDict):
    name: str
    permissions: list[str]


SYSTEM_ROLES: dict[str, RoleSpec] = {
    "tenant_admin": {
        "name": "企业管理员",
        "permissions": [
            "tenant:read",
            "tenant:settings:manage",
            "user:manage",
            "role:read",
            "role:manage",
            "role:assign",
            "org:read",
            "org:manage",
            "asset:*",
            "opportunity:*",
            "project:*",
            "knowledge:*",
            "template:*",
            "model:use",
            "model:read_metadata",
            "model:manage",
            "model:manage_key",
            "model:policy:manage",
            "audit:read",
            "compliance:review",
            "compliance:break_glass",
            "compliance:export",
        ],
    },
    "bid_manager": {
        "name": "投标负责人",
        "permissions": [
            "opportunity:*",
            "asset:read",
            "project:*",
            "knowledge:read",
            "knowledge:use",
            "knowledge:write",
            "template:read",
            "template:use",
            "template:copy",
            "template:write",
            "model:use",
            "org:read",
        ],
    },
    "bid_engineer": {
        "name": "标书工程师",
        "permissions": [
            "opportunity:read",
            "asset:read",
            "project:read",
            "project:write",
            "knowledge:read",
            "knowledge:use",
            "knowledge:write",
            "template:read",
            "template:use",
            "template:copy",
            "template:write",
            "model:use",
            "org:read",
        ],
    },
    "compliance": {
        "name": "合规/法务",
        "permissions": [
            "opportunity:read",
            "asset:read",
            "project:read",
            "project:approve",
            "audit:read",
            "knowledge:read",
            "knowledge:use",
            "template:read",
            "template:use",
            "template:copy",
            "model:use",
            "org:read",
            "compliance:review",
            "compliance:break_glass",
            "compliance:export",
        ],
    },
}


# 企业版扩展点：overlay 提供 roles_extra.py 时注入额外系统角色（如平台管理员）。
try:  # noqa: SIM105
    from app.modules.identity.roles_extra import EXTRA_ROLES as _EXTRA_ROLES
except ImportError:
    _EXTRA_ROLES: dict[str, RoleSpec] = {}

SYSTEM_ROLES.update(_EXTRA_ROLES)
