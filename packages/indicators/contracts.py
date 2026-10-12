from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class IndicatorSnapshot(BaseModel):
    as_of: datetime
    interval: str
    bar_count: int
    sma_20: Decimal | None = None
    sma_50: Decimal | None = None
    ema_20: Decimal | None = None
    rsi_14: Decimal | None = None
    macd_line: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    atr_14: Decimal | None = None
    adx_14: Decimal | None = None
    plus_di_14: Decimal | None = None
    minus_di_14: Decimal | None = None
    bollinger_upper_20: Decimal | None = None
    bollinger_middle_20: Decimal | None = None
    bollinger_lower_20: Decimal | None = None
    session_vwap: Decimal | None = None
    momentum_10: Decimal | None = None
    rate_of_change_10_pct: Decimal | None = None
    annualized_realized_volatility_20_pct: Decimal | None = None
    unavailable: list[str]
