from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from packages.indicators.calculations import calculate_indicators
from packages.market_data.contracts import HistoricalBar


def _bars(count: int = 60) -> list[HistoricalBar]:
    start = datetime(2026, 8, 1, 9, 15, tzinfo=timezone.utc)
    return [
        HistoricalBar(
            timestamp=start + timedelta(hours=index),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(98 + index),
            close=Decimal(100 + index),
            volume=1000 + index,
        )
        for index in range(count)
    ]


def test_calculates_price_indicators_and_realized_volatility():
    result = calculate_indicators(_bars(), "1h")

    assert result.bar_count == 60
    assert result.sma_20 == Decimal("149.5")
    assert result.sma_50 == Decimal("134.5")
    assert result.rsi_14 == Decimal(100)
    assert result.atr_14 == Decimal(4)
    assert result.adx_14 is not None and result.adx_14 > 99
    assert result.bollinger_upper_20 is not None
    assert result.session_vwap is not None
    assert result.momentum_10 == Decimal(10)
    assert result.rate_of_change_10_pct is not None
    assert result.annualized_realized_volatility_20_pct is not None
    assert "sma_50" not in result.unavailable


def test_short_history_leaves_long_lookback_indicators_unavailable():
    result = calculate_indicators(_bars(10), "1d")

    assert result.sma_20 is None
    assert result.macd_signal is None
    assert result.adx_14 is None
    assert result.annualized_realized_volatility_20_pct is None
    assert "sma_20" in result.unavailable


def test_rejects_out_of_order_bars():
    bars = _bars(3)
    bars[1], bars[2] = bars[2], bars[1]

    with pytest.raises(ValueError, match="ascending timestamps"):
        calculate_indicators(bars, "1d")
