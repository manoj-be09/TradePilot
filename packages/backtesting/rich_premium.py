"""Iron condors sold only on days when the nearest straddle looks rich. Research only.

Richness compares the move the at-the-money straddle implies until expiry with a forecast of the
move until expiry, built from past sessions' 5-minute parity changes and overnight gaps. Each break
before expiry counts as one more session, because prices carry weekends and holidays. A reading of 1
means the straddle charges about what recent movement predicts.

The session before a break is skipped unless it is expiry day: a trade closed before the break does
not collect the premium priced for it, and one held through it carries the gap. Bars must be regular
hours only, so the loader has to leave out special sessions. Results are simulated, not a forecast.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import log, sqrt
from statistics import mean

from packages.backtesting.global_strategies import _last_closed, centre_strike, strike_at_delta
from packages.backtesting.published_strategies import (
    _BAR,
    _ENTRY_RETRIES,
    _ENTRY_TIME,
    _INTRADAY_EXIT_BAR,
    _OPENING_BAR,
    _SQUARE_OFF_BAR,
    Book,
    Leg,
    Market,
    PublishedRule,
    TradeResult,
    _at,
    simulate,
    spot_from_closed_bars,
)

# The widest round wing one lot of 65 can carry inside the ₹20,000 per-trade limit at any credit.
WING_POINTS = 300
_MINIMUM_READINGS = 40
_FORECAST_SESSIONS = 22
_RECENT_SESSIONS = 5
_MINIMUM_PAST_SESSIONS = 5
_VARIANCE_FLOOR = 1e-8
_SESSION_MINUTES = 375
# An at-the-money straddle costs about 0.8 standard deviations of the move to expiry.
_STRADDLE_PER_MOVE = 0.8
_LARGEST_CENTRE_GAP = 75.0
_SMALLEST_DELTA_TOLERANCE = 0.012
_DELTA_TOLERANCE_SHARE = 0.25
_CONDOR = (Leg(0, "CE", "SELL"), Leg(0, "PE", "SELL"), Leg(0, "CE", "BUY"), Leg(0, "PE", "BUY"))


@dataclass(frozen=True)
class SessionMove:
    session: date
    book: Book
    last: float
    variance: float


def _nearest_book(market: Market, session: date) -> Book | None:
    """The nearest expiry with a full day of bars, so thin special sessions never count."""
    for book in market.books:
        if (book.expiry >= session
                and len(book.moments_by_day.get(session, ())) >= _MINIMUM_READINGS):
            return book
    return None


def session_moves(market: Market) -> list[SessionMove]:
    """Each usable session's squared 5-minute parity changes plus its squared overnight gap."""
    moves: list[SessionMove] = []
    for session in market.sessions:
        book = _nearest_book(market, session)
        if book is None:
            continue
        readings = [spot for moment in book.moments_by_day[session]
                    if (spot := spot_from_closed_bars(book, moment + _BAR)) is not None]
        if len(readings) < _MINIMUM_READINGS:
            continue
        previous = moves[-1] if moves else None
        base = None
        if previous is not None:
            # The same expiry's last reading, so a change of expiry does not look like a gap.
            for moment in reversed(book.moments_by_day.get(previous.session, ())):
                base = spot_from_closed_bars(book, moment + _BAR)
                if base is not None:
                    break
            base = base or previous.last
        gap = log(readings[0] / base) if base else 0.0
        changes = sum(log(after / before) ** 2
                      for before, after in zip(readings, readings[1:], strict=False))
        moves.append(SessionMove(session, book, readings[-1], changes + gap * gap))
    return moves


def forecast_variance(past: Sequence[SessionMove]) -> float | None:
    """One session's variance, blended over the last session, week and month before the decision."""
    if len(past) < _MINIMUM_PAST_SESSIONS:
        return None
    variances = [move.variance for move in past[-_FORECAST_SESSIONS:]]
    blend = (variances[-1] + mean(variances[-_RECENT_SESSIONS:]) + mean(variances)) / 3
    return max(blend, _VARIANCE_FLOOR)


def sessions_until_expiry(book: Book, decided_at: datetime) -> float:
    """Trading time left in sessions, with each break before expiry counted as one more."""
    remaining = [day for day in sorted(book.moments_by_day) if day >= decided_at.date()]
    breaks = sum(1 for day, later in zip(remaining, remaining[1:], strict=False)
                 if (later - day).days > 1)
    opened = _OPENING_BAR.hour * 60 + _OPENING_BAR.minute
    minutes = decided_at.hour * 60 + decided_at.minute - opened
    elapsed = max(0.0, min(1.0, minutes / _SESSION_MINUTES))
    return len(remaining) - elapsed + breaks


def read_richness(
    book: Book, decided_at: datetime, past: Sequence[SessionMove],
) -> float | None:
    """Move implied by the straddle over the move forecast, from bars closed by the decision."""
    forecast = forecast_variance(past)
    spot = spot_from_closed_bars(book, decided_at)
    if forecast is None or spot is None:
        return None
    strike = centre_strike(book, spot)
    if abs(float(strike) - spot) > _LARGEST_CENTRE_GAP:
        return None
    call = _last_closed(book, strike, "CE", decided_at)
    put = _last_closed(book, strike, "PE", decided_at)
    if call is None or put is None:
        return None
    implied_move = (call + put) / (_STRADDLE_PER_MOVE * spot)
    return implied_move / sqrt(forecast * sessions_until_expiry(book, decided_at))


def condor_strikes(
    book: Book, decided_at: datetime, short_delta: float,
) -> list[Decimal] | None:
    """Sold call and put by delta, then bought wings a fixed distance beyond them."""
    spot = spot_from_closed_bars(book, decided_at)
    if spot is None:
        return None
    tolerance = max(_SMALLEST_DELTA_TOLERANCE, short_delta * _DELTA_TOLERANCE_SHARE)
    call = strike_at_delta(book, "CE", decided_at, spot, short_delta, tolerance)
    put = strike_at_delta(book, "PE", decided_at, spot, short_delta, tolerance)
    if call is None or put is None:
        return None
    far_call, far_put = call + WING_POINTS, put - WING_POINTS
    if (far_call, "CE") not in book.bars or (far_put, "PE") not in book.bars:
        return None
    return [call, put, far_call, far_put]


def is_before_break(book: Book, session: date, following: date | None) -> bool:
    return (session != book.expiry and following is not None
            and (following - session).days > 1)


def exit_bar_for(
    book: Book, session: date, following: date | None, hold_overnight: bool,
) -> datetime | None:
    """Same day on expiry day and the day before it; otherwise the next close if held overnight."""
    if session == book.expiry:
        return _at(session, _SQUARE_OFF_BAR)
    if not hold_overnight or following == book.expiry:
        return _at(session, _INTRADAY_EXIT_BAR)
    if following is None or following not in book.moments_by_day:
        return None
    return _at(following, _INTRADAY_EXIT_BAR)


def run_rich_condor(
    short_delta: float, threshold: float, hold_overnight: bool, entry_delay_bars: int = 0,
) -> Callable[[Market], list[TradeResult]]:
    def run(market: Market) -> list[TradeResult]:
        moves = session_moves(market)
        results = []
        for at, move in enumerate(moves):
            following = moves[at + 1].session if at + 1 < len(moves) else None
            if is_before_break(move.book, move.session, following):
                continue
            richness = read_richness(move.book, _at(move.session, _ENTRY_TIME),
                                     moves[max(0, at - _FORECAST_SESSIONS):at])
            if richness is None or richness < threshold:
                continue
            exit_bar = exit_bar_for(move.book, move.session, following, hold_overnight)
            if exit_bar is None:
                continue
            decided_at = _at(move.session, _ENTRY_TIME) + _BAR * entry_delay_bars
            strikes = condor_strikes(move.book, decided_at, short_delta)
            if strikes is None:
                continue
            # A thin wing may print no bar at the decision; the strikes stay fixed for three more.
            for attempt in range(_ENTRY_RETRIES + 1):
                result = simulate(move.book, _CONDOR, strikes, decided_at + _BAR * attempt,
                                  exit_bar, market.half_spread_pct, quiet_exit=True)
                if result is not None:
                    results.append(result)
                    break
        return results
    return run


def rich_rules(entry_delay_bars: int = 0) -> tuple[PublishedRule, ...]:
    """The three rules fixed before the later half was read for them."""
    return (
        PublishedRule("rich_condor_day", "This study: rich days, 30-delta, out the same day",
                      run_rich_condor(0.3, 1.0, False, entry_delay_bars), True),
        PublishedRule("rich_condor_next_close",
                      "This study: rich days, 30-delta, out at the next close unless expiry",
                      run_rich_condor(0.3, 1.0, True, entry_delay_bars), True),
        PublishedRule("very_rich_condor_next_close",
                      "This study: very rich days, 20-delta, for the higher win rate",
                      run_rich_condor(0.2, 1.1, True, entry_delay_bars), True),
    )


RICH_RULES = rich_rules()
