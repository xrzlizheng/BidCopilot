from app.db.repository import TenantRepository
from app.modules.notifications.models import Notification


class NotificationRepository(TenantRepository[Notification]):
    model = Notification
