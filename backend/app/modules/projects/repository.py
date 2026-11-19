from app.db.repository import TenantRepository
from app.modules.projects.models import BidProject


class ProjectRepository(TenantRepository[BidProject]):
    model = BidProject
