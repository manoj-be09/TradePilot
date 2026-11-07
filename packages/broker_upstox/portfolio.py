"""Read-only broker positions and funds, and the reconciliation check (LIVE_TRADING.md L3).

Uses only GET endpoints through the market-data adapter's transport, which refuses any
other method. The token is the owner's order-scoped Upstox token, passed in by the caller;
it is never read from `.env` here.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from packages.market_data.contracts import MarketDataProviderError
from packages.market_data.upstox import UpstoxMarketDataProvider

POSITIONS_PATH = "/v2/portfolio/short-term-positions"
FUNDS_PATH = "/v2/user/get-funds-and-margin"
FO_PREFIX = "NSE_FO|"


class PortfolioReader(UpstoxMarketDataProvider):
    """Same GET-only `_send` as market data; a different token and two read paths."""

    async def fo_positions(self) -> dict[str, int]:
        rows = await self._get(POSITIONS_PATH)
        if not isinstance(rows, list):
            raise MarketDataProviderError("Upstox positions response was not a list")
        out: dict[str, int] = {}
        for row in rows:
            key, quantity = _field(row, "instrument_token"), _field(row, "quantity")
            if not isinstance(key, str) or not isinstance(quantity, int):
                raise MarketDataProviderError("Upstox position row is missing key or quantity")
            if key.startswith(FO_PREFIX):
                out[key] = out.get(key, 0) + quantity
        return {key: quantity for key, quantity in out.items() if quantity}

    async def available_margin(self) -> Decimal:
        data = await self._get(FUNDS_PATH)
        try:
            return Decimal(str(data["equity"]["available_margin"]))
        except (KeyError, TypeError, ArithmeticError) as error:
            raise MarketDataProviderError("Upstox funds response had no available margin") \
                from error


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else None


@dataclass(frozen=True)
class PositionDifference:
    instrument_key: str
    broker_quantity: int
    expected_quantity: int


def reconcile_positions(broker: dict[str, int],
                        expected: dict[str, int]) -> list[PositionDifference]:
    """Every F&O instrument whose net quantity differs; empty means matched."""
    keys = sorted(set(broker) | set(expected))
    return [PositionDifference(k, broker.get(k, 0), expected.get(k, 0))
            for k in keys if broker.get(k, 0) != expected.get(k, 0)]
