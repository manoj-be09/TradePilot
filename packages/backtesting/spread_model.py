"""Half-spread model for candle backtests, calibrated from recorded Feed V3 quotes.

Historical candles carry no bid/ask, so backtest fills move the bar close against the
trade by a half-spread. This fits that half-spread per premium band from our own
recorded option quotes, using an upper quantile so fills stay conservative. A band
with too few observations keeps the configured default instead of a thin estimate.
Recorded book times are Upstox message times, not exchange-origin book times.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SPREAD_MODEL_ID = "recorded-half-spread-by-premium-v1"
# Lower premium bound (rupees) of each band; the last band is open-ended.
PREMIUM_BANDS = (Decimal(0), Decimal(10), Decimal(25), Decimal(50), Decimal(100),
                 Decimal(200))
MIN_OBSERVATIONS = 30
QUANTILE = Decimal("0.75")
_PCT = Decimal("0.0001")


@dataclass(frozen=True)
class SpreadBand:
    lower: Decimal
    upper: Decimal | None
    observations: int
    median_pct: Decimal | None
    quantile_pct: Decimal | None
    half_spread_pct: Decimal
    source: str  # RECORDED or DEFAULT

    def contains(self, price: Decimal) -> bool:
        return price >= self.lower and (self.upper is None or price < self.upper)

    def snapshot(self) -> dict[str, Any]:
        return {
            "lower": str(self.lower),
            "upper": str(self.upper) if self.upper is not None else None,
            "observations": self.observations,
            "median_pct": str(self.median_pct) if self.median_pct is not None else None,
            "quantile_pct": str(self.quantile_pct) if self.quantile_pct is not None else None,
            "half_spread_pct": str(self.half_spread_pct),
            "source": self.source,
        }


@dataclass(frozen=True)
class SpreadModel:
    bands: tuple[SpreadBand, ...]
    default_pct: Decimal
    quantile: Decimal
    min_observations: int
    rejected_quotes: int

    def half_spread_pct(self, price: Decimal) -> Decimal:
        for band in self.bands:
            if band.contains(price):
                return band.half_spread_pct
        return self.default_pct

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_id": SPREAD_MODEL_ID,
            "default_pct": str(self.default_pct),
            "quantile": str(self.quantile),
            "min_observations": self.min_observations,
            "rejected_quotes": self.rejected_quotes,
            "bands": [band.snapshot() for band in self.bands],
        }


def observed_half_spread(
    bid: Decimal | None, ask: Decimal | None
) -> tuple[Decimal, Decimal] | None:
    """(mid, half-spread % of mid) for a usable two-sided quote; None when empty or crossed."""
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return mid, (ask - bid) / 2 / mid * 100


def _quantile(sorted_values: list[Decimal], q: Decimal) -> Decimal:
    """Nearest-rank quantile, so the result is always an observed value."""
    rank = max(1, int((q * len(sorted_values)).to_integral_value(rounding=ROUND_HALF_UP)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def calibrate_spreads(
    quotes: Iterable[tuple[Decimal | None, Decimal | None]],
    default_pct: Decimal,
    *,
    min_observations: int = MIN_OBSERVATIONS,
    quantile: Decimal = QUANTILE,
) -> SpreadModel:
    if default_pct <= 0 or not Decimal(0) < quantile < Decimal(1) or min_observations < 1:
        raise ValueError("Spread calibration needs a positive default, a quantile in (0, 1) "
                         "and a positive minimum sample")
    per_band: list[list[Decimal]] = [[] for _ in PREMIUM_BANDS]
    rejected = 0
    for bid, ask in quotes:
        observed = observed_half_spread(bid, ask)
        if observed is None:
            rejected += 1
            continue
        mid, pct = observed
        index = max(i for i, lower in enumerate(PREMIUM_BANDS) if mid >= lower)
        per_band[index].append(pct)
    bands = []
    for index, lower in enumerate(PREMIUM_BANDS):
        upper = PREMIUM_BANDS[index + 1] if index + 1 < len(PREMIUM_BANDS) else None
        values = sorted(per_band[index])
        if len(values) >= min_observations:
            median = _quantile(values, Decimal("0.5")).quantize(_PCT)
            upper_q = _quantile(values, quantile).quantize(_PCT)
            bands.append(SpreadBand(lower, upper, len(values), median, upper_q, upper_q,
                                    "RECORDED"))
        else:
            bands.append(SpreadBand(lower, upper, len(values), None, None, default_pct,
                                    "DEFAULT"))
    return SpreadModel(tuple(bands), default_pct, quantile, min_observations, rejected)
