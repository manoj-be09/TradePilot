"""Descriptive NIFTY conditions from completed daily bars, never a forecast."""

from datetime import date, datetime
from decimal import Decimal
from math import log, sqrt
from statistics import stdev
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from packages.market_data.contracts import HistoricalBar

IST = ZoneInfo("Asia/Kolkata")


class MarketOutlook(BaseModel):
    status: Literal["ready", "unavailable"]
    provider: str
    instrument_key: str
    benchmark: str = "NIFTY 50"
    horizon: str = "completed daily sessions"
    reason: str | None = None
    as_of: datetime | None = None
    session_date: date | None = None
    observations: int = 0
    last_close: Decimal | None = None
    previous_close: Decimal | None = None
    last_session_change_pct: Decimal | None = None
    twenty_session_change_pct: Decimal | None = None
    recent_direction: Literal["Rising", "Falling", "Mixed"] | None = None
    realized_volatility_20d_pct: Decimal | None = None
    relative_volatility_score: int | None = None
    relative_volatility_label: Literal["Low", "Typical", "Elevated", "Very elevated"] | None = None
    reasons: list[str] = Field(default_factory=list)


def unavailable(provider: str, instrument_key: str, reason: str) -> MarketOutlook:
    return MarketOutlook(
        status="unavailable", provider=provider, instrument_key=instrument_key, reason=reason
    )


def describe_market(
    bars: list[HistoricalBar], *, provider: str, instrument_key: str,
    expected_session: date,
) -> MarketOutlook:
    """Exclude unfinished/future sessions and refuse stale or malformed history."""
    if any(bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None for bar in bars):
        return unavailable(
            provider, instrument_key, "Daily history has timestamps without time zones."
        )
    completed = [
        bar for bar in bars if bar.timestamp.astimezone(IST).date() <= expected_session
    ]
    if len(completed) < 121:
        return unavailable(
            provider, instrument_key, "At least 121 completed daily bars are needed."
        )
    dates = [bar.timestamp.astimezone(IST).date() for bar in completed]
    if any(left >= right for left, right in zip(dates, dates[1:], strict=False)):
        return unavailable(
            provider, instrument_key, "Daily history contains duplicate or unordered sessions."
        )
    if dates[-1] != expected_session:
        return unavailable(
            provider, instrument_key, "The latest completed NSE session is missing."
        )
    if any(
        bar.close <= 0 or bar.open <= 0 or bar.low <= 0
        or bar.high < max(bar.open, bar.close, bar.low)
        or bar.low > min(bar.open, bar.close)
        for bar in completed
    ):
        return unavailable(provider, instrument_key, "Daily history contains invalid prices.")

    closes = [float(bar.close) for bar in completed]
    rolling = []
    for end in range(20, len(closes)):
        returns = [log(closes[i] / closes[i - 1]) for i in range(end - 19, end + 1)]
        rolling.append(stdev(returns) * sqrt(252) * 100)
    history = rolling[:-1][-100:]
    if len(history) < 60:
        return unavailable(
            provider, instrument_key, "Insufficient earlier windows for a volatility comparison."
        )
    current = rolling[-1]
    score = round(100 * (sum(v < current for v in history) +
                         0.5 * sum(v == current for v in history)) / len(history))
    label: Literal["Low", "Typical", "Elevated", "Very elevated"] = (
        "Low" if score <= 25 else "Typical" if score <= 75
        else "Elevated" if score <= 90 else "Very elevated"
    )
    twenty_change = 100 * (closes[-1] / closes[-21] - 1)
    sma20 = sum(closes[-20:]) / 20
    direction: Literal["Rising", "Falling", "Mixed"] = (
        "Rising" if twenty_change > 0 and closes[-1] > sma20
        else "Falling" if twenty_change < 0 and closes[-1] < sma20
        else "Mixed"
    )
    return MarketOutlook(
        status="ready", provider=provider, instrument_key=instrument_key,
        as_of=completed[-1].timestamp, session_date=dates[-1],
        observations=len(completed), last_close=completed[-1].close,
        previous_close=completed[-2].close,
        last_session_change_pct=Decimal(str(100 * (closes[-1] / closes[-2] - 1))),
        twenty_session_change_pct=Decimal(str(twenty_change)),
        recent_direction=direction,
        realized_volatility_20d_pct=Decimal(str(current)),
        relative_volatility_score=score, relative_volatility_label=label,
        reasons=[
            "Direction describes the past 20 sessions: the close versus its 20-session "
            "average and close 20 sessions ago.",
            "Volatility is measured from the last 20 daily returns; its score ranks this "
            "reading against up to 100 earlier windows.",
        ],
    )
