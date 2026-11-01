"""What was knowable about NIFTY when a weekly trade was armed. Research only.

Every reading uses bars that had already closed at the decision moment: the same-day option bars
before it, and session closes from earlier days. Candles carry no index series, so spot comes from
put-call parity. Implied volatility is the usual at-the-money straddle approximation, not a fitted
surface, so it is a rough reading of how expensive options were.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import log, sqrt
from statistics import mean, stdev

from packages.backtesting.history_backtest import (
    _IST,
    ExpiryHistory,
    _entry_day,
    _ist,
    synthetic_spot,
)

DECISION_TIME = time(9, 20)
_BAR_LENGTH = timedelta(minutes=5)
_SESSIONS_A_YEAR = 252
_REALIZED_SESSIONS = 10
_MOMENTUM_SESSIONS = 5
_TREND_SESSIONS = 20
_RANK_WINDOW = 52
_RANK_MINIMUM = 8
_STRADDLE_TO_VOLATILITY = 0.8
_SKEW_DISTANCE = Decimal("0.02")


@dataclass(frozen=True)
class EntrySignal:
    expiry: date
    decided_at: datetime
    spot: float
    implied_volatility: float
    realized_volatility: float
    # Implied over realized volatility: how much options charged over recent movement.
    volatility_premium: float
    momentum: float
    # The five-session move in units of recent daily movement.
    move_size: float
    above_trend: bool
    # Out-of-the-money put minus call, as a share of the straddle.
    put_skew: float
    premium_rank: float | None = None
    skew_rank: float | None = None


def _latest_before(
    history: ExpiryHistory, moment: datetime,
) -> tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]:
    """The last close per strike from bars of the same session that had closed by then."""
    calls: dict[Decimal, Decimal] = {}
    puts: dict[Decimal, Decimal] = {}
    for (strike, kind), candles in history.bars.items():
        closed = [
            candle for candle in candles
            if _ist(candle.start).date() == moment.date()
            and _ist(candle.start) + _BAR_LENGTH <= moment
        ]
        if closed:
            (calls if kind == "CE" else puts)[strike] = closed[-1].close
    return calls, puts


def session_closes(histories: Sequence[ExpiryHistory]) -> dict[date, float]:
    """Parity spot at each session's last bar, from the nearest expiry trading that day."""
    closes: dict[date, float] = {}
    for history in sorted(histories, key=lambda item: item.expiry, reverse=True):
        by_moment: dict[datetime, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
        for (strike, kind), candles in history.bars.items():
            for candle in candles:
                pair = by_moment.setdefault(_ist(candle.start), ({}, {}))
                pair[0 if kind == "CE" else 1][strike] = candle.close
        last_of_day: dict[date, datetime] = {}
        for moment in sorted(by_moment):
            last_of_day[moment.date()] = moment
        for day, moment in last_of_day.items():
            spot = synthetic_spot(*by_moment[moment])
            if spot is not None:
                closes[day] = float(spot)
    return closes


def _nearest(strikes: Sequence[Decimal], level: Decimal) -> Decimal:
    return min(strikes, key=lambda strike: (abs(strike - level), strike))


def read_signal(
    history: ExpiryHistory, closes: dict[date, float], entry_days: int,
) -> EntrySignal | None:
    """None when the history before the decision is too short to say anything."""
    entry_day = _entry_day(history, entry_days)
    if entry_day is None:
        return None
    decided_at = datetime.combine(entry_day, DECISION_TIME, tzinfo=_IST)
    earlier = [closes[day] for day in sorted(closes) if day < entry_day]
    if len(earlier) < max(_TREND_SESSIONS, _REALIZED_SESSIONS + 1, _MOMENTUM_SESSIONS + 1):
        return None
    calls, puts = _latest_before(history, decided_at)
    parity = synthetic_spot(calls, puts)
    common = sorted(set(calls) & set(puts))
    if parity is None or not common:
        return None
    centre = _nearest(common, parity)
    straddle = float(calls[centre] + puts[centre])
    expires_at = datetime.combine(history.expiry, time(15, 30), tzinfo=_IST)
    years = (expires_at - decided_at).total_seconds() / (365 * 86400)
    if straddle <= 0 or years <= 0:
        return None
    spot = float(parity)
    recent = earlier[-(_REALIZED_SESSIONS + 1):]
    daily = stdev(log(after / before) for before, after in zip(recent, recent[1:], strict=False))
    if daily <= 0:
        return None
    implied = straddle / (_STRADDLE_TO_VOLATILITY * spot * sqrt(years))
    realized = daily * sqrt(_SESSIONS_A_YEAR)
    momentum = log(earlier[-1] / earlier[-1 - _MOMENTUM_SESSIONS])
    put_strike = _nearest(sorted(puts), parity * (1 - _SKEW_DISTANCE))
    call_strike = _nearest(sorted(calls), parity * (1 + _SKEW_DISTANCE))
    return EntrySignal(
        expiry=history.expiry, decided_at=decided_at, spot=spot, implied_volatility=implied,
        realized_volatility=realized, volatility_premium=implied / realized, momentum=momentum,
        move_size=momentum / (daily * sqrt(_MOMENTUM_SESSIONS)),
        above_trend=earlier[-1] > mean(earlier[-_TREND_SESSIONS:]),
        put_skew=float(puts[put_strike] - calls[call_strike]) / straddle,
    )


def trailing_rank(value: float, earlier: Sequence[float]) -> float | None:
    """Share of the last year's weekly readings below this one; None until enough exist."""
    window = list(earlier)[-_RANK_WINDOW:]
    if len(window) < _RANK_MINIMUM:
        return None
    return sum(item < value for item in window) / len(window)


def read_signals(histories: Sequence[ExpiryHistory], entry_days: int) -> dict[date, EntrySignal]:
    """Signals by expiry, oldest first, each ranked only against the ones before it."""
    closes = session_closes(histories)
    signals: dict[date, EntrySignal] = {}
    premiums: list[float] = []
    skews: list[float] = []
    for history in sorted(histories, key=lambda item: item.expiry):
        signal = read_signal(history, closes, entry_days)
        if signal is None:
            continue
        signals[history.expiry] = replace(
            signal,
            premium_rank=trailing_rank(signal.volatility_premium, premiums),
            skew_rank=trailing_rank(signal.put_skew, skews),
        )
        premiums.append(signal.volatility_premium)
        skews.append(signal.put_skew)
    return signals
