from app.db.repository import TenantRepository
from app.modules.opportunities.models import (
    AwardRecord,
    BuyerWatchlist,
    Opportunity,
    OpportunityBuyerIntel,
    OpportunityFavorite,
    OpportunityMatch,
    OpportunitySubscription,
)
from app.modules.system.models import TenantSetting


class OpportunityRepository(TenantRepository[Opportunity]):
    model = Opportunity


class FavoriteRepository(TenantRepository[OpportunityFavorite]):
    model = OpportunityFavorite


class AwardRepository(TenantRepository[AwardRecord]):
    model = AwardRecord


class SubscriptionRepository(TenantRepository[OpportunitySubscription]):
    model = OpportunitySubscription


class BuyerIntelRepository(TenantRepository[OpportunityBuyerIntel]):
    model = OpportunityBuyerIntel


class BuyerWatchlistRepository(TenantRepository[BuyerWatchlist]):
    model = BuyerWatchlist


class OpportunityMatchRepository(TenantRepository[OpportunityMatch]):
    model = OpportunityMatch


class TenantSettingRepository(TenantRepository[TenantSetting]):
    model = TenantSetting
