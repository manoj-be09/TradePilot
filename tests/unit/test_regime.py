from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.market_data.contracts import HistoricalBar
from packages.regime.classifier import classify_market_regime


def _bars(count: int, *, trend: bool = True) -> list[HistoricalBar]:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = []
    for index in range(count):
        close = Decimal(100 + index if trend else 100)
        result.append(
            HistoricalBar(
                timestamp=start + timedelta(days=index),
                open=close,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=1000,
            )
        )
    return result


def test_upward_directional_market_is_labeled_trending_up():
    result = classify_market_regime(_bars(60), "1d")

    assert result.regime == "TRENDING_UP"
    assert result.directional_bias == "UP"
    assert result.adx_14 is not None and result.adx_14 >= 25
    assert result.realized_volatility_annualized_percent is not None
    assert result.prior_20_bar_high == Decimal(160)
    assert result.prior_20_bar_low == Decimal(137)
    assert result.reasons


def test_short_history_is_marked_uncertain_without_probability_score():
    result = classify_market_regime(_bars(10), "1d")

    assert result.regime == "UNCERTAIN"
    assert result.directional_bias == "NEUTRAL"
    assert result.realized_volatility_percentile is None
    assert result.prior_20_bar_high is None
    assert result.prior_20_bar_low is None
