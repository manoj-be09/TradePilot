"""Transparent daily-bar context for research ordering, never an execution signal."""

from __future__ import annotations

from statistics import mean
from typing import Any

from packages.market_data.contracts import HistoricalBar

FITS: dict[str, dict[str, set[str]]] = {
    "trend": {
        "direction": {"rising"},
        "volatility": {"typical", "elevated"},
        "volume": {"normal", "high"},
    },
    "breakout": {
        "direction": {"rising", "mixed"},
        "volatility": {"typical", "elevated"},
        "volume": {"high"},
    },
    "pullback": {
        "direction": {"rising"},
        "volatility": {"low", "typical"},
        "volume": {"normal", "high"},
    },
    "reversion": {
        "direction": {"mixed"},
        "volatility": {"low", "typical"},
        "volume": {"low", "normal"},
    },
    "momentum": {
        "direction": {"rising"},
        "volatility": {"typical", "elevated"},
        "volume": {"high"},
    },
}
# Adaptation context is an explicit platform heuristic, not the source author's claim.
FITS.update({
    "channel20": FITS["breakout"],
    "channel55": FITS["trend"],
    "connors_r2": FITS["pullback"],
    "band_reclaim": FITS["reversion"],
    "sma_cross": FITS["trend"],
})


def _atr_percent(bars: list[HistoricalBar], end: int) -> float:
    ranges = [
        max(
            float(current.high - current.low),
            abs(float(current.high - previous.close)),
            abs(float(current.low - previous.close)),
        )
        for previous, current in zip(bars[end - 15 : end - 1], bars[end - 14 : end], strict=True)
    ]
    return mean(ranges) / float(bars[end - 1].close) * 100


def describe_stock_context(bars: list[HistoricalBar]) -> dict[str, Any]:
    """Use completed daily candles only; callers validate ordering and provenance."""
    if len(bars) < 51:
        raise ValueError("At least 51 completed daily candles are required")
    closes = [float(bar.close) for bar in bars]
    last = closes[-1]
    sma20 = mean(closes[-20:])
    prior_sma20 = mean(closes[-21:-1])
    change20 = (last / closes[-21] - 1) * 100
    if last > sma20 > prior_sma20 and change20 > 1:
        direction = "rising"
    elif last < sma20 < prior_sma20 and change20 < -1:
        direction = "falling"
    else:
        direction = "mixed"

    current_atr = _atr_percent(bars, len(bars))
    previous = [_atr_percent(bars, end) for end in range(max(16, len(bars) - 100), len(bars))]
    percentile = round(100 * sum(value <= current_atr for value in previous) / len(previous))
    volatility = (
        "low"
        if percentile < 25
        else "typical"
        if percentile < 75
        else "elevated"
        if percentile < 90
        else "extreme"
    )

    prior_volume = mean(float(bar.volume) for bar in bars[-21:-1])
    latest_volume = float(bars[-1].volume)
    volume_ratio = latest_volume / prior_volume if prior_volume > 0 and latest_volume > 0 else None
    volume = (
        "unknown"
        if volume_ratio is None
        else "low"
        if volume_ratio < 0.7
        else "high"
        if volume_ratio >= 1.5
        else "normal"
    )
    return {
        "as_of": bars[-1].timestamp,
        "direction": direction,
        "change_20d_pct": round(change20, 2),
        "volatility": volatility,
        "atr_14_pct": round(current_atr, 2),
        "volatility_percentile": percentile,
        "volume": volume,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "basis": "Selected stock's completed daily candles; no broad-market or intraday forecast",
    }


def fit_for_strategy(key: str, context: dict[str, Any]) -> tuple[int, list[str]]:
    """Ordinal fit only: a matching regime is not evidence of future returns."""
    expected = FITS[key]
    score = 0
    reasons: list[str] = []
    for factor, weight in (("direction", 2), ("volatility", 1), ("volume", 1)):
        actual = str(context[factor])
        if actual == "unknown":
            reasons.append(f"{factor.title()} unavailable; no fit credit")
        elif actual in expected[factor]:
            score += weight
            reasons.append(f"{factor.title()} {actual} matches the documented setup")
        else:
            score -= weight
            reasons.append(f"{factor.title()} {actual} does not match the documented setup")
    return score, reasons
