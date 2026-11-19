from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.base import Base


class TenantRepository[T: Base]:
    model: type[T]

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def scoped(self, statement: Select[Any]) -> Select[Any]:
        return statement.where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]

    async def get_or_404(self, entity_id: UUID) -> T:
        statement = self.scoped(select(self.model).where(self.model.id == entity_id))  # type: ignore[attr-defined]
        entity = (await self.session.execute(statement)).scalar_one_or_none()
        if entity is None:
            raise NotFoundError()
        return entity
