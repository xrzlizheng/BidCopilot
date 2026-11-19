from app.db.repository import TenantRepository
from app.modules.opportunities.models import AwardRecord, Opportunity
from app.modules.pricing.models import PriceQuote, PriceStrategy
from app.modules.projects.models import BidDecisionRun, BidProject, BidResult
from app.modules.system.models import TenantSetting


class PriceStrategyRepository(TenantRepository[PriceStrategy]):
    model = PriceStrategy


class PriceQuoteRepository(TenantRepository[PriceQuote]):
    model = PriceQuote


class PricingSettingRepository(TenantRepository[TenantSetting]):
    model = TenantSetting


class PricingBidResultRepository(TenantRepository[BidResult]):
    model = BidResult


class PricingBidDecisionRunRepository(TenantRepository[BidDecisionRun]):
    model = BidDecisionRun


class PricingProjectRepository(TenantRepository[BidProject]):
    model = BidProject


class PricingAwardRepository(TenantRepository[AwardRecord]):
    model = AwardRecord


class PricingOpportunityRepository(TenantRepository[Opportunity]):
    model = Opportunity
