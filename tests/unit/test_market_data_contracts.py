import asyncio

import pytest

from packages.market_data.contracts import MarketDataUnavailable, UnconfiguredMarketDataProvider


def test_unconfigured_provider_does_not_fabricate_quote_data():
    provider = UnconfiguredMarketDataProvider()
    with pytest.raises(MarketDataUnavailable):
        asyncio.run(provider.get_quote("token-from-instrument-master"))
    health = asyncio.run(provider.health_check())
    assert not health.configured
    assert not health.healthy
