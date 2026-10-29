"""Choose the NIFTY instruments to record: index, India VIX, futures and an option window."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from packages.market_data.contracts import DiscoveredInstrument
from packages.market_data.upstox import UpstoxMarketDataProvider, UpstoxOptionContract

NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
INDIA_VIX_KEY = "NSE_INDEX|India VIX"
FEED_FULL_MODE_LIMIT = 1500  # Upstox combined full-mode limit; stay well inside it.


@dataclass(frozen=True)
class RecordedSpec:
    instrument_key: str
    kind: str  # INDEX, FUT or OPT
    trading_symbol: str | None = None
    expiry: date | None = None
    strike: Decimal | None = None
    option_type: str | None = None
    lot_size: int | None = None
    tick_size: Decimal | None = None


@dataclass(frozen=True)
class UniverseConfig:
    weekly_expiries: int = 3
    futures_count: int = 3
    strikes_each_side: int = 20
    recenter_margin: int = 5


def nifty_futures(rows: Iterable[DiscoveredInstrument], today: date) -> list[RecordedSpec]:
    futures = sorted(
        (row for row in rows
         if row.instrument_type == "FUT" and row.trading_symbol.startswith("NIFTY FUT ")
         and row.expiry is not None and row.expiry >= today),
        key=lambda row: row.expiry or today,
    )
    return [
        RecordedSpec(
            instrument_key=row.instrument_key, kind="FUT", trading_symbol=row.trading_symbol,
            expiry=row.expiry, lot_size=row.lot_size, tick_size=row.tick_size,
        )
        for row in futures
    ]


def select_options(
    contracts: Iterable[UpstoxOptionContract],
    *,
    today: date,
    spot: Decimal,
    monthly_expiries: Iterable[date],
    config: UniverseConfig,
) -> list[RecordedSpec]:
    """Nearest weekly expiries plus listed monthlies, ATM ± N strikes, both sides."""
    by_expiry: dict[date, list[UpstoxOptionContract]] = {}
    for contract in contracts:
        if contract.expiry >= today:
            by_expiry.setdefault(contract.expiry, []).append(contract)
    expiries = sorted(by_expiry)[: config.weekly_expiries]
    expiries += [day for day in sorted(set(monthly_expiries)) if day in by_expiry]
    selected: list[RecordedSpec] = []
    for expiry in sorted(set(expiries)):
        rows = by_expiry[expiry]
        for strike in strike_window(
            sorted({row.strike for row in rows}), spot, config.strikes_each_side
        ):
            for row in rows:
                if row.strike == strike:
                    selected.append(RecordedSpec(
                        instrument_key=row.instrument_key, kind="OPT",
                        trading_symbol=row.trading_symbol, expiry=row.expiry,
                        strike=row.strike, option_type=row.option_type,
                        lot_size=row.lot_size, tick_size=row.tick_size,
                    ))
    return selected


def strike_window(strikes: list[Decimal], spot: Decimal, each_side: int) -> list[Decimal]:
    if not strikes:
        return []
    atm = min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot), strikes[i]))
    return strikes[max(0, atm - each_side): atm + each_side + 1]


def needs_recenter(
    specs: Iterable[RecordedSpec], spot: Decimal, config: UniverseConfig
) -> bool:
    """True when spot is within `recenter_margin` strikes of any expiry's window edge."""
    by_expiry: dict[date, list[Decimal]] = {}
    for spec in specs:
        if spec.kind == "OPT" and spec.expiry is not None and spec.strike is not None:
            by_expiry.setdefault(spec.expiry, []).append(spec.strike)
    for strikes in by_expiry.values():
        ordered = sorted(set(strikes))
        atm = min(range(len(ordered)), key=lambda i: abs(ordered[i] - spot))
        if atm < config.recenter_margin or atm >= len(ordered) - config.recenter_margin:
            return True
    return False


async def discover(
    provider: UpstoxMarketDataProvider, today: date, spot: Decimal, config: UniverseConfig
) -> list[RecordedSpec]:
    futures = nifty_futures(
        await provider.search_instruments("NIFTY FUT", exchange="NSE", segment="FO"), today
    )[: config.futures_count]
    options = select_options(
        await provider.get_option_contracts("NIFTY"),
        today=today, spot=spot,
        monthly_expiries=[spec.expiry for spec in futures if spec.expiry is not None],
        config=config,
    )
    specs = [
        RecordedSpec(NIFTY_INDEX_KEY, "INDEX", "NIFTY 50"),
        RecordedSpec(INDIA_VIX_KEY, "INDEX", "INDIA VIX"),
        *futures,
        *options,
    ]
    if len(specs) > FEED_FULL_MODE_LIMIT:
        raise ValueError("Recorder universe exceeds the Feed V3 full-mode subscription limit")
    return specs
