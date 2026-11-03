"""Load imported NIFTY option candles into backtest books, and the recorded spread bands.

Shared by the research scripts and the nightly rule engine. The data is private and local.
"""

import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import cast

from sqlalchemy import select

from packages.backtesting.history_backtest import _ist
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY
from packages.backtesting.published_strategies import Bar, Book, Kind
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.database.session import build_session_factory

_FIRST_EXPIRY = date(2024, 10, 1)
_RECORDED_SPREADS = DEFAULT_RESULTS_DIRECTORY / "cal-bear_call_spread.json"
_REGULAR_OPEN = time(9, 15)
_REGULAR_CLOSE = time(15, 30)


def load_books(
    database_url: str, last_expiry: date, extra_database_urls: Sequence[str] = (),
    regular_hours_only: bool = False, first_expiry: date = _FIRST_EXPIRY,
) -> list[Book]:
    bars: dict[date, dict[tuple[Decimal, Kind], dict[datetime, Bar]]] = {}
    moments: dict[datetime, datetime] = {}
    for url in (database_url, *extra_database_urls):
        with build_session_factory(url)() as session:
            rows = session.execute(
                select(HistoricalOptionBar.expiry, HistoricalOptionBar.strike,
                       HistoricalOptionBar.option_type, HistoricalOptionBar.bar_start,
                       HistoricalOptionBar.open, HistoricalOptionBar.high, HistoricalOptionBar.low,
                       HistoricalOptionBar.close)
                .join(HistoricalImportContract, (
                    HistoricalImportContract.instrument_key == HistoricalOptionBar.instrument_key
                ) & (HistoricalImportContract.interval == HistoricalOptionBar.interval))
                .where(HistoricalOptionBar.interval == "5m",
                       HistoricalImportContract.status == "COMPLETE",
                       HistoricalOptionBar.expiry >= max(first_expiry, _FIRST_EXPIRY),
                       HistoricalOptionBar.expiry <= last_expiry)
            )
            for expiry, strike, kind, start, opened, high, low, closed in rows:
                moment = moments.get(start)
                if moment is None:
                    moment = moments[start] = _ist(start)
                if regular_hours_only and not _REGULAR_OPEN <= moment.time() < _REGULAR_CLOSE:
                    continue
                series = bars.setdefault(expiry, {}).setdefault((strike, cast(Kind, kind)), {})
                series[moment] = Bar(float(opened), float(high), float(low), float(closed))
    return [Book(expiry, by_key) for expiry, by_key in sorted(bars.items())]


def recorded_spread(band_field: str) -> Callable[[float], float]:
    saved = json.loads(_RECORDED_SPREADS.read_text(encoding="utf-8"))
    bands = [(float(band["lower"]), float(band[band_field]))
             for band in saved["config"]["spread_model"]["bands"]]

    def half_spread(price: float) -> float:
        return next(pct for lower, pct in reversed(bands) if price >= lower)
    return half_spread


