from apps.api.settings import get_settings
from packages.market_data.contracts import MarketDataProvider, UnconfiguredMarketDataProvider
from packages.market_data.upstox import UpstoxMarketDataProvider


def get_market_data_provider() -> MarketDataProvider:
    token = get_settings().upstox_analytics_token
    if token is None or not token.get_secret_value().strip():
        return UnconfiguredMarketDataProvider()
    return UpstoxMarketDataProvider(token.get_secret_value().strip())
