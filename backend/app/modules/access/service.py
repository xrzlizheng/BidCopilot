from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import ForbiddenError
from app.core.permissions import has_permission
from app.modules.access.capabilities import is_capability
from app.modules.identity.schemas import CurrentUser


class DenyReason(StrEnum):
    NO_CAPABILITY = "no_capability"
    UNKNOWN_CAPABILITY = "unknown_capability"


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason_code: str | None = None


class AuthzService:
    @staticmethod
    def can_capability(principal: CurrentUser, capability: str) -> Decision:
        if not is_capability(capability):
            return Decision(False, DenyReason.UNKNOWN_CAPABILITY)
        if not has_permission(principal.permissions, capability):
            return Decision(False, DenyReason.NO_CAPABILITY)
        return Decision(True)

    @staticmethod
    def can_capability_in_org(
        principal: CurrentUser, org_unit_id, capability: str
    ) -> Decision:
        if not is_capability(capability):
            return Decision(False, DenyReason.UNKNOWN_CAPABILITY)
        if has_permission(principal.permissions, capability):
            return Decision(True)
        for scope_id, capabilities in principal.org_capabilities:
            if scope_id == org_unit_id and has_permission(list(capabilities), capability):
                return Decision(True)
        return Decision(False, DenyReason.NO_CAPABILITY)

    @classmethod
    def require_capability(cls, principal: CurrentUser, capability: str) -> None:
        decision = cls.can_capability(principal, capability)
        if not decision.allow:
            raise ForbiddenError(
                f"missing permission: {capability}",
                code=decision.reason_code or "forbidden",
            )

    @classmethod
    def require_all_capabilities(
        cls, principal: CurrentUser, capabilities: list[str] | tuple[str, ...]
    ) -> None:
        for capability in capabilities:
            cls.require_capability(principal, capability)
