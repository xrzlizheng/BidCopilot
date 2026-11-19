from app.db.repository import TenantRepository
from app.modules.system.models import TenantSetting


class SystemSettingRepository(TenantRepository[TenantSetting]):
    model = TenantSetting