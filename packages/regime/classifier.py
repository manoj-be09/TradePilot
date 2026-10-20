from datetime import datetime
from decimal import Decimal
from math import log, sqrt
from statistics import stdev
from typing import Literal

from pydantic import BaseModel

from packages.indicators.calculations import annualization_factor, calculate_indicators
from packages.market_data.contracts import HistoricalBar

MarketRegime = Literal[
    "TRENDING_UP",
    "TRENDING_DOWN",
    "RANGE_BOUND",
    "HIGH_VOLATILITY",
    "LOW_VOLATILITY",
    "BREAKOUT",
    "BREAKDOWN",
    "UNCERTAIN",
]


class MarketRegimeResult(BaseModel):
    as_of: datetime
    regime: MarketRegime
    directional_bias: Literal["UP", "DOWN", "NEUTRAL"]
    adx_14: Decimal | None
    realized_volatility_percentile: Decimal | None
    reasons: list[str]
    method: list[str]
    realized_volatility_annualized_percent: Decimal | None = None
    prior_20_bar_high: Decimal | None = None
    prior_20_bar_low: Decimal | None = None


def _rolling_volatility_percentiles(
    bars: list[HistoricalBar], interval: str, period: int = 20
) -> tuple[Decimal | None, Decimal | None]:
    annualization = sqrt(annualization_factor(interval))
    closes = [float(bar.close) for bar in bars]
    if any(close <= 0 for close in closes):
        return None, None
    rolling: list[float] = []
    for end in range(period + 1, len(closes) + 1):
        sample = closes[end - period - 1 : end]
        returns = [log(sample[index] / sample[index - 1]) for index in range(1, len(sample))]
        rolling.append(stdev(returns) * annualization * 100)
    if len(rolling) < 2:
        return (Decimal(str(rolling[-1])) if rolling else None), None
    historical = sorted(rolling[:-1][-100:])
    current = rolling[-1]
    less = sum(value < current for value in historical)
    equal = sum(value == current for value in historical)
    percentile = (less + equal / 2) * 100 / len(historical)
    return Decimal(str(current)), Decimal(str(percentile))


def classify_market_regime(
    bars: list[HistoricalBar], interval: str, breakout_lookback: int = 20
) -> MarketRegimeResult:
    if not bars:
        raise ValueError("At least one historical bar is required")
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:], strict=False)):
        raise ValueError("Historical bars must have unique, ascending timestamps")
    indicators = calculate_indicators(bars, interval)
    adx = indicators.adx_14
    current_close = bars[-1].close
    bias: Literal["UP", "DOWN", "NEUTRAL"] = "NEUTRAL"
    reasons: list[str] = []
    regime: MarketRegime = "UNCERTAIN"
    prior_high: Decimal | None = None
    prior_low: Decimal | None = None

    if len(bars) > breakout_lookback:
        previous = bars[-breakout_lookback - 1 : -1]
        prior_high = max(bar.high for bar in previous)
        prior_low = min(bar.low for bar in previous)
        if current_close > prior_high:
            regime, bias = "BREAKOUT", "UP"
            reasons.append(f"Close is above the prior {breakout_lookback}-bar high.")
        elif current_close < prior_low:
            regime, bias = "BREAKDOWN", "DOWN"
            reasons.append(f"Close is below the prior {breakout_lookback}-bar low.")

    current_volatility, volatility_percentile = _rolling_volatility_percentiles(bars, interval)
    if regime == "UNCERTAIN" and volatility_percentile is not None:
        if volatility_percentile > 80:
            regime = "HIGH_VOLATILITY"
            reasons.append("Realized volatility is above the 80th percentile of recent windows.")
    if regime == "UNCERTAIN" and adx is not None:
        if adx >= 25 and indicators.plus_di_14 is not None and indicators.minus_di_14 is not None:
            if indicators.plus_di_14 > indicators.minus_di_14:
                regime, bias = "TRENDING_UP", "UP"
                reasons.append("ADX is at least 25 and positive directional movement dominates.")
            elif indicators.minus_di_14 > indicators.plus_di_14:
                regime, bias = "TRENDING_DOWN", "DOWN"
                reasons.append("ADX is at least 25 and negative directional movement dominates.")
        elif adx < 20:
            regime = "RANGE_BOUND"
            reasons.append("ADX is below 20 and no breakout condition was detected.")

    if regime == "UNCERTAIN" and volatility_percentile is not None:
        if volatility_percentile < 20:
            regime = "LOW_VOLATILITY"
            reasons.append(
                "Realized volatility is below the 20th percentile of recent rolling windows."
            )

    if not reasons:
        reasons.append(
            "Insufficient history or mixed conditions; the classifier leaves the regime uncertain."
        )
    if current_volatility is None:
        reasons.append(
            "Realized-volatility percentile is unavailable without enough positive closes."
        )
    return MarketRegimeResult(
        as_of=bars[-1].timestamp,
        regime=regime,
        directional_bias=bias,
        adx_14=adx,
        realized_volatility_percentile=volatility_percentile,
        realized_volatility_annualized_percent=current_volatility,
        prior_20_bar_high=prior_high,
        prior_20_bar_low=prior_low,
        reasons=reasons,
        method=[
            "Breakout/breakdown compares the latest close with preceding lookback highs and lows.",
            "Volatility uses the latest 20-return value and up to 100 earlier rolling windows.",
            "Trend labels use ADX >= 25 and DI direction; RANGE_BOUND uses ADX < 20.",
            "Labels are heuristics, not calibrated probabilities or trading signals.",
        ],
    )
