from contextvars import ContextVar
from typing import Any

current_user: ContextVar[Any | None] = ContextVar("bidcopilot_current_user", default=None)
