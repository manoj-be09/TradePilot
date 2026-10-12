import math
from decimal import Decimal
from statistics import stdev
from zoneinfo import ZoneInfo

from packages.indicators.contracts import IndicatorSnapshot
from packages.market_data.contracts import HistoricalBar

_INDIA_TZ = ZoneInfo("Asia/Kolkata")
_ZERO = Decimal(0)


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, _ZERO) / Decimal(len(values))


def _ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = _mean(values[:period])
    result[period - 1] = current
    alpha = Decimal(2) / Decimal(period + 1)
    for index in range(period, len(values)):
        current = values[index] * alpha + current * (Decimal(1) - alpha)
        result[index] = current
    return result


def _rsi(values: list[Decimal], period: int) -> Decimal | None:
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    if len(changes) < period:
        return None
    gains = [max(change, _ZERO) for change in changes]
    losses = [max(-change, _ZERO) for change in changes]
    average_gain = _mean(gains[:period])
    average_loss = _mean(losses[:period])
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return Decimal(50) if average_gain == 0 else Decimal(100)
    relative_strength = average_gain / average_loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)


def _directional_indicators(
    bars: list[HistoricalBar], period: int
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    true_ranges: list[Decimal] = []
    positive_moves: list[Decimal] = []
    negative_moves: list[Decimal] = []
    for previous, current in zip(bars, bars[1:], strict=False):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        positive_moves.append(up_move if up_move > down_move and up_move > 0 else _ZERO)
        negative_moves.append(down_move if down_move > up_move and down_move > 0 else _ZERO)

    if len(true_ranges) < period:
        return None, None, None, None

    smoothed_range = sum(true_ranges[:period], _ZERO)
    smoothed_positive = sum(positive_moves[:period], _ZERO)
    smoothed_negative = sum(negative_moves[:period], _ZERO)
    dx_values: list[Decimal] = []
    atr = plus_di = minus_di = None
    for index in range(period - 1, len(true_ranges)):
        if index >= period:
            smoothed_range = smoothed_range - smoothed_range / period + true_ranges[index]
            smoothed_positive = (
                smoothed_positive - smoothed_positive / period + positive_moves[index]
            )
            smoothed_negative = (
                smoothed_negative - smoothed_negative / period + negative_moves[index]
            )
        atr = smoothed_range / period
        if smoothed_range == 0:
            plus_di = minus_di = Decimal(0)
            dx_values.append(Decimal(0))
            continue
        plus_di = Decimal(100) * smoothed_positive / smoothed_range
        minus_di = Decimal(100) * smoothed_negative / smoothed_range
        denominator = plus_di + minus_di
        dx_values.append(
            Decimal(0) if denominator == 0 else Decimal(100) * abs(plus_di - minus_di) / denominator
        )

    if len(dx_values) < period or atr is None or plus_di is None or minus_di is None:
        return atr, None, plus_di, minus_di
    adx = _mean(dx_values[:period])
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period
    return atr, adx, plus_di, minus_di


def annualization_factor(interval: str) -> float:
    bars_per_day = {"1m": 375.0, "5m": 75.0, "15m": 25.0, "30m": 12.5, "1h": 6.25}
    if interval == "1d":
        return 252.0
    if interval not in bars_per_day:
        raise ValueError(f"Unsupported interval: {interval}")
    return bars_per_day[interval] * 252


def _realized_volatility_pct(values: list[Decimal], interval: str) -> Decimal | None:
    if len(values) < 21 or any(value <= 0 for value in values):
        return None
    changes = [
        math.log(float(values[index] / values[index - 1])) for index in range(1, len(values))
    ]
    if len(changes) < 2:
        return None
    annualized = stdev(changes) * math.sqrt(annualization_factor(interval)) * 100
    return Decimal(str(annualized))


def calculate_indicators(bars: list[HistoricalBar], interval: str) -> IndicatorSnapshot:
    """Calculate documented trailing indicators; missing lookback data stays null."""
    if not bars:
        raise ValueError("At least one historical bar is required")
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:], strict=False)):
        raise ValueError("Historical bars must have unique, ascending timestamps")
    annualization_factor(interval)

    closes = [bar.close for bar in bars]
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    ema20_series = _ema_series(closes, 20)
    ema20 = ema20_series[-1]
    rsi14 = _rsi(closes, 14)

    fast_ema = _ema_series(closes, 12)
    slow_ema = _ema_series(closes, 26)
    macd_values = [
        fast - slow
        for fast, slow in zip(fast_ema, slow_ema, strict=True)
        if fast is not None and slow is not None
    ]
    signal_series = _ema_series(macd_values, 9)
    macd_line = macd_values[-1] if macd_values else None
    macd_signal = signal_series[-1] if signal_series else None
    macd_histogram = (
        macd_line - macd_signal if macd_line is not None and macd_signal is not None else None
    )

    atr14, adx14, plus_di14, minus_di14 = _directional_indicators(bars, 14)
    bollinger_middle: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_lower: Decimal | None
    if sma20 is not None:
        bollinger_middle = sma20
        variance = _mean([(value - sma20) ** 2 for value in closes[-20:]])
        standard_deviation = variance.sqrt()
        bollinger_upper = bollinger_middle + Decimal(2) * standard_deviation
        bollinger_lower = bollinger_middle - Decimal(2) * standard_deviation
    else:
        bollinger_middle = bollinger_upper = bollinger_lower = None

    last_timestamp = bars[-1].timestamp
    session_date = (
        last_timestamp.astimezone(_INDIA_TZ).date()
        if last_timestamp.tzinfo is not None
        else last_timestamp.date()
    )
    session_bars = [
        bar
        for bar in bars
        if (
            bar.timestamp.astimezone(_INDIA_TZ).date()
            if bar.timestamp.tzinfo is not None
            else bar.timestamp.date()
        )
        == session_date
    ]
    volume_total = sum((bar.volume for bar in session_bars), 0)
    session_vwap = None
    if volume_total > 0:
        weighted_typical = sum(
            ((bar.high + bar.low + bar.close) / Decimal(3) * bar.volume for bar in session_bars),
            _ZERO,
        )
        session_vwap = weighted_typical / volume_total

    momentum10 = closes[-1] - closes[-11] if len(closes) >= 11 else None
    roc10 = (
        (closes[-1] / closes[-11] - Decimal(1)) * Decimal(100)
        if len(closes) >= 11 and closes[-11] != 0
        else None
    )
    realized20 = _realized_volatility_pct(closes[-21:], interval)

    values = {
        "sma_20": sma20,
        "sma_50": sma50,
        "ema_20": ema20,
        "rsi_14": rsi14,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "atr_14": atr14,
        "adx_14": adx14,
        "plus_di_14": plus_di14,
        "minus_di_14": minus_di14,
        "bollinger_upper_20": bollinger_upper,
        "bollinger_middle_20": bollinger_middle,
        "bollinger_lower_20": bollinger_lower,
        "session_vwap": session_vwap,
        "momentum_10": momentum10,
        "rate_of_change_10_pct": roc10,
        "annualized_realized_volatility_20_pct": realized20,
    }
    return IndicatorSnapshot(
        as_of=bars[-1].timestamp,
        interval=interval,
        bar_count=len(bars),
        unavailable=[name for name, value in values.items() if value is None],
        **values,
    )
