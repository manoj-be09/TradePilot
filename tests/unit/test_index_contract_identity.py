import asyncio
from datetime import date

import httpx
import pytest

from packages.market_data.contracts import MarketDataProviderError
from packages.market_data.index_catalog import OptionIndex
from packages.market_data.upstox import UpstoxMarketDataProvider


@pytest.mark.parametrize("key,underlying,accepted", [
    ("BSE_FO|example", "BSE_INDEX|SENSEX", True),
    ("NSE_FO|example", "BSE_INDEX|SENSEX", False),
    ("BSE_FO|example", "BSE_INDEX|BANKEX", False),
])
def test_selected_index_contracts_cannot_borrow_other_index_or_venue(
    monkeypatch, key, underlying, accepted,
):
    async def resolve(symbol):
        assert symbol == "SENSEX"
        return OptionIndex(symbol="SENSEX", name="SENSEX", instrument_key="BSE_INDEX|SENSEX",
                           exchange="BSE", derivatives_exchange="BFO")

    monkeypatch.setattr("packages.market_data.upstox.resolve_option_index", resolve)

    def handler(request):
        assert request.url.params["instrument_key"] == "BSE_INDEX|SENSEX"
        return httpx.Response(200, json={"status": "success", "data": [{
            "instrument_key": key, "underlying_key": underlying,
            "trading_symbol": "SENSEX example CE", "expiry": "2026-10-15",
            "strike_price": 75000, "instrument_type": "CE", "lot_size": 20,
            "tick_size": 0.05,
        }]})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    if accepted:
        result = asyncio.run(provider.get_option_contracts("SENSEX", date(2026, 10, 15)))
        assert result[0].lot_size == 20
        assert result[0].instrument_key == key
    else:
        with pytest.raises(MarketDataProviderError, match="does not match"):
            asyncio.run(provider.get_option_contracts("SENSEX", date(2026, 10, 15)))
