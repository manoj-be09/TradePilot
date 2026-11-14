"""Entry metrics from the clock and the recorder's minute snapshots. Simulated paper only.

India VIX and the NIFTY index are already recorded each minute into `market_snapshots_1m`.
A reading older than `FRESH` is treated as missing, so a condition on it stays unknown and
the entry waits (fail closed) rather than trading on a stale number.
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import log, sqrt
from statistics import stdev
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.database.recorder_models import MarketSnapshot1m
from packages.market_data.contracts import (
    MarketDataProvider,
    MarketDataProviderError,
    MarketDataUnavailable,
)
from packages.paper_trading.rules import RuleMetric
from packages.recorder.universe import INDIA_VIX_KEY, NIFTY_INDEX_KEY

_IST = ZoneInfo("Asia/Kolkata")
_OPEN = time(9, 15)
_CENT = Decimal("0.01")
FRESH = timedelta(minutes=3)


def clock_metrics(as_of: datetime, expiry: date) -> dict[RuleMetric, Decimal | None]:
    """Minutes since the 09:15 IST open and calendar days to expiry, at the quote time."""
    local = as_of.astimezone(_IST)
    opened = datetime.combine(local.date(), _OPEN, tzinfo=_IST)
    return {
        "minutes_since_open": Decimal(int((local - opened).total_seconds() // 60)),
        "days_to_expiry": Decimal((expiry - local.date()).days),
    }


def _pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or not whole:
        return None
    return (part / whole * 100).quantize(_CENT)


def recorded_index_metrics(session: Session, as_of: datetime) -> dict[RuleMetric, Decimal | None]:
    """India VIX level and change, and NIFTY's change and range today, from fresh snapshots."""
    utc = as_of.astimezone(timezone.utc)
    day = as_of.astimezone(_IST).date()

    def latest(key: str) -> MarketSnapshot1m | None:
        return session.scalar(
            select(MarketSnapshot1m)
            .where(MarketSnapshot1m.instrument_key == key,
                   MarketSnapshot1m.trading_day == day,
                   MarketSnapshot1m.bucket_start <= utc,
                   MarketSnapshot1m.bucket_start > utc - FRESH,
                   MarketSnapshot1m.ltp_close.is_not(None))
            .order_by(MarketSnapshot1m.bucket_start.desc()).limit(1)
        )

    vix, nifty = latest(INDIA_VIX_KEY), latest(NIFTY_INDEX_KEY)
    high = low = None
    if nifty is not None:
        high, low = session.execute(
            select(func.max(MarketSnapshot1m.ltp_high), func.min(MarketSnapshot1m.ltp_low))
            .where(MarketSnapshot1m.instrument_key == NIFTY_INDEX_KEY,
                   MarketSnapshot1m.trading_day == day,
                   MarketSnapshot1m.bucket_start <= utc)
        ).one()
    def change(row: MarketSnapshot1m | None) -> Decimal | None:
        if row is None or row.ltp_close is None or row.prev_close is None:
            return None
        return _pct(row.ltp_close - row.prev_close, row.prev_close)

    return {
        "india_vix": vix.ltp_close if vix is not None else None,
        "india_vix_change_pct": change(vix),
        "nifty_change_pct": change(nifty),
        "nifty_range_pct": _pct(high - low, nifty.prev_close)
        if nifty is not None and high is not None and low is not None else None,
    }


# Realized volatility needs a clean run of daily closes, which the recorder's gaps can't give, so
# it comes from the provider's daily NIFTY candles, once per day per process.
VOLATILITY_METRICS: frozenset[str] = frozenset({"realized_vol_10d_pct", "iv_minus_rv_points"})
RV_SESSIONS = 10
_TRADING_DAYS = 252
_rv_cache: dict[date, Decimal | None] = {}


def realized_vol_pct(closes: Sequence[Decimal]) -> Decimal | None:
    """Annualized close-to-close volatility, in percent, of the last RV_SESSIONS returns."""
    usable = [float(close) for close in closes if close > 0][-(RV_SESSIONS + 1):]
    if len(usable) < RV_SESSIONS + 1:
        return None
    returns = [log(after / before) for before, after in zip(usable, usable[1:], strict=False)]
    return Decimal(str(stdev(returns) * sqrt(_TRADING_DAYS) * 100)).quantize(_CENT)


async def volatility_metrics(
    provider: MarketDataProvider, as_of: datetime, atm_iv_pct: Decimal | None,
) -> dict[RuleMetric, Decimal | None]:
    """10-session realized volatility before today, and at-the-money IV less it, in points.

    Unknown (so the entry waits) when the candles can't be read or a session is missing.
    """
    day = as_of.astimezone(_IST).date()
    if day not in _rv_cache:
        try:
            bars = await provider.get_historical(
                NIFTY_INDEX_KEY, as_of - timedelta(days=30), as_of, "1d")
        except (MarketDataProviderError, MarketDataUnavailable):
            # shortcut: a failure is retried on the next check, not cached; fine while rare.
            return {"realized_vol_10d_pct": None, "iv_minus_rv_points": None}
        before_today = sorted((bar for bar in bars if bar.timestamp.astimezone(_IST).date() < day),
                              key=lambda bar: bar.timestamp)
        _rv_cache.clear()  # only today's value is ever needed
        _rv_cache[day] = realized_vol_pct([bar.close for bar in before_today])
    rv = _rv_cache[day]
    return {
        "realized_vol_10d_pct": rv,
        "iv_minus_rv_points": (atm_iv_pct - rv).quantize(_CENT)
        if rv is not None and atm_iv_pct is not None else None,
    }
