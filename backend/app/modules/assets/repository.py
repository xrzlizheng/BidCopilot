from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import TenantRepository
from app.modules.assets.models import Company


class CompanyRepository(TenantRepository[Company]):
    model = Company


class AssetRepository(TenantRepository[Any]):
    def __init__(self, session: AsyncSession, tenant_id: UUID, model: Any) -> None:
        super().__init__(session, tenant_id)
        self.model = model
