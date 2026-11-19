"""配额策略 seam：项目/席位/AI 篇数/AI 字数上限。

社区版为不限量空实现；企业版用 overlay 覆盖同路径文件，
转发到 platform.plan_service 的 tenant_plans 套餐计量（超限 429）。
调用方只依赖本模块的函数签名，不感知版本差异。
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


async def enforce_project_limit(session: AsyncSession, tenant_id: Any) -> None:
    return None


async def enforce_seat_limit(session: AsyncSession, tenant_id: Any) -> None:
    return None


async def enforce_ai_piece_limit(
    session: AsyncSession, tenant_id: Any, *, amount: int = 1
) -> None:
    return None


async def enforce_ai_word_limit(
    session: AsyncSession, tenant_id: Any, *, amount: int = 0
) -> None:
    return None


@asynccontextmanager
async def project_creation_guard(tenant_id: Any) -> AsyncIterator[None]:
    # 企业版为「配额检查 + 租户级互斥锁」防并发绕过；社区版不限量，直通。
    yield